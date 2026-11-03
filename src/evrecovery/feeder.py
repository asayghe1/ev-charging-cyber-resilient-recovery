"""Public IEEE feeder AC replay and a local signed affine approximation.

Only transformer nameplate thermal limits are available from these sources.
See docs/FEEDER.md for the operating-point changes and approximation limits.
"""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
import numpy as np
import opendssdirect as odd

_DATA = Path(__file__).resolve().parents[2] / 'data' / 'feeders'
_CASES = {
    'ieee13': ('13Bus/IEEE13Nodeckt.dss',
               [('632', 1), ('671', 2), ('675', 3), ('680', 1), ('645', 2), ('611', 3), ('633', 1), ('652', 1)], 100.),
    'ieee123': ('123Bus/IEEE123Master.dss',
                [('48', 1), ('76', 2), ('83', 3), ('67', 1), ('95', 2), ('105', 3), ('49', 1), ('65', 2)], 200.),
}


class FeederModel:
    """A separate DSS engine with fixed controls and unity-PF EV load groups.

    ``background_scale=1`` means 80% of the published background kW/kvar.
    Regulator set points are 120 V and taps are frozen after the nominal solve.
    One instance is intended for sequential use; different instances own contexts.
    """

    def __init__(self, name: str = 'ieee123', n_stations: int = 6):
        if name not in _CASES:
            raise ValueError(f'unknown feeder {name!r}; choose ieee13 or ieee123')
        if not isinstance(n_stations, (int, np.integer)) or not 1 <= n_stations <= 8:
            raise ValueError('n_stations must be an integer from 1 through 8')
        master, candidates, maximum = _CASES[name]
        self.name, self.n_stations = name, n_stations
        self.station_buses = tuple(b for b, _ in candidates[:n_stations])
        self.station_phases = tuple(p for _, p in candidates[:n_stations])
        self.station_names = tuple(f'ev_{i}' for i in range(n_stations))
        self.nominal_station_kw = np.full(n_stations, maximum)
        self.background_base_multiplier = 0.8
        self.dss = odd.NewContext()
        self.dss.Basic.AllowChangeDir(False)
        self.dss.Text.Command(f'compile [{(_DATA / master).resolve()}]')
        self.dss.Text.Command('set mode=snapshot maxiterations=100 maxcontroliter=100 tolerance=1e-9')
        self._background = []
        for load in self.dss.Loads.AllNames():
            self.dss.Loads.Name(load)
            kw, kvar = self.dss.Loads.kW(), self.dss.Loads.kvar()
            self._background.append((load, kw * 0.8, kvar * 0.8))
        self._last_background = None
        self._set_background(1.)
        for reg in self.dss.RegControls.AllNames():
            self.dss.RegControls.Name(reg)
            self.dss.RegControls.ForwardVreg(120.)
        self.dss.Solution.Solve()
        if not self.dss.Solution.Converged():
            raise RuntimeError(f'{name} nominal initialization did not converge')
        self.frozen_taps = {}
        for transformer in self.dss.Transformers.AllNames():
            self.dss.Transformers.Name(transformer)
            taps = []
            for winding in range(1, self.dss.Transformers.NumWindings() + 1):
                self.dss.Transformers.Wdg(winding)
                taps.append(self.dss.Transformers.Tap())
            self.frozen_taps[transformer] = taps
        self.dss.Text.Command('set controlmode=off')
        for station, bus, phase in zip(self.station_names, self.station_buses, self.station_phases):
            self.dss.Circuit.SetActiveBus(bus)
            if phase not in self.dss.Bus.Nodes():
                raise RuntimeError(f'station {station} uses missing phase {bus}.{phase}')
            kv = self.dss.Bus.kVBase()
            self.dss.Text.Command(f'new load.{station} bus1={bus}.{phase} phases=1 conn=wye '
                                  f'kv={kv:.12g} kw=0 kvar=0 model=1 vminpu=0.5 vmaxpu=1.5')
        self._voltage_names = tuple(self.dss.Circuit.AllNodeNames())
        # Current nameplate of each winding; neutral currents are excluded.
        self._thermal = []
        self.thermal_ratings = []
        for transformer in self.dss.Transformers.AllNames():
            self.dss.Transformers.Name(transformer)
            phases = self.dss.CktElement.NumPhases()
            ncond = self.dss.CktElement.NumConductors()
            nodes = self.dss.CktElement.NodeOrder()
            for winding in range(1, self.dss.Transformers.NumWindings() + 1):
                self.dss.Transformers.Wdg(winding)
                kva, kv = self.dss.Transformers.kVA(), self.dss.Transformers.kV()
                amps = kva / kv / (np.sqrt(3.) if phases == 3 else 1.)
                if kva <= 0 or kv <= 0:
                    raise RuntimeError('source transformer lacks a positive nameplate')
                for conductor in range(phases):
                    idx = (winding - 1) * ncond + conductor
                    row = f'transformer.{transformer}:w{winding}:node{nodes[idx]}'
                    self._thermal.append((transformer, idx, amps, row))
                self.thermal_ratings.append(dict(asset=f'transformer.{transformer}', winding=winding,
                                                kva=kva, kv=kv, phase_current_a=float(amps),
                                                provenance='explicit source transformer winding kVA and kV'))
        self.unrated_lines = tuple(self.dss.Lines.AllNames())
        self.solve_count = 0
        self.total_solve_seconds = 0.
        self._linear_cache = {}
        initial = self.evaluate(np.zeros(n_stations))
        if not (initial['converged'] and initial['v_min'] >= .95 and initial['v_max'] <= 1.05
                and initial['loading_max'] <= 1.):
            raise RuntimeError(f'{name} configured zero-EV initial point is infeasible: {initial}')

    def _set_background(self, scale: float):
        if scale != self._last_background:
            for name, kw, kvar in self._background:
                self.dss.Loads.Name(name)
                self.dss.Loads.kW(kw * scale)
                self.dss.Loads.kvar(kvar * scale)
            self._last_background = scale

    def evaluate(self, power_kw, background_scale: float = 1.0) -> dict:
        """Run nonlinear AC power flow; power is total kW for each phase group.

        Detailed ``loading_pu`` contains each transformer winding's phase current
        divided by its source nameplate current. Lines are explicitly unrated.
        ``solve_seconds`` times engine solving, ``evaluation_seconds`` the API call.
        """
        started = perf_counter()
        power = np.asarray(power_kw, dtype=float)
        if power.shape != (self.n_stations,) or not np.all(np.isfinite(power)) or np.any(power < 0):
            raise ValueError('power_kw must have one finite nonnegative value per station')
        scale = float(background_scale)
        if not np.isfinite(scale) or scale < 0:
            raise ValueError('background_scale must be finite and nonnegative')
        self._set_background(scale)
        for station, kw in zip(self.station_names, power):
            self.dss.Loads.Name(station)
            self.dss.Loads.kW(float(kw))
            self.dss.Loads.kvar(0.)
        solve_start = perf_counter()
        self.dss.Solution.Solve()
        elapsed = perf_counter() - solve_start
        self.solve_count += 1
        self.total_solve_seconds += elapsed
        voltage = np.asarray(self.dss.Circuit.AllBusMagPu(), dtype=float)
        current_cache = {}
        loading = []
        for transformer, idx, amps, _ in self._thermal:
            if transformer not in current_cache:
                self.dss.Circuit.SetActiveElement(f'transformer.{transformer}')
                current_cache[transformer] = np.asarray(self.dss.CktElement.CurrentsMagAng())[::2]
            loading.append(current_cache[transformer][idx] / amps)
        loading = np.asarray(loading)
        return dict(converged=bool(self.dss.Solution.Converged()), v_min=float(voltage.min()),
                    v_max=float(voltage.max()), loading_max=float(loading.max()),
                    voltage_pu=voltage, voltage_names=self._voltage_names,
                    loading_pu=loading, loading_names=tuple(x[3] for x in self._thermal),
                    power_kw=power.copy(), background_scale=scale,
                    solve_seconds=elapsed, evaluation_seconds=perf_counter() - started,
                    thermal_scope='source-nameplate transformer winding phase current; lines unrated')

    @staticmethod
    def _signed_outputs(result):
        return np.r_[-result['voltage_pu'], result['voltage_pu'], result['loading_pu']]

    def linear_constraints(self, background_scale=1.0, p0=None, vmin=.95, vmax=1.05,
                           loading_limit=1., voltage_margin=.002) -> dict:
        """Return signed local rows ``A @ p + C * background_scale <= b``.

        Centered finite differences use 1 kW station steps when p0>=1 kW,
        otherwise a forward difference. Background step is 0.001. The returned
        intercept makes the prediction exact at (p0, background_scale). No
        sensitivity is clipped: robust box evaluation must handle both signs.
        This approximation is not a nonlinear AC safety certificate.
        """
        point = np.zeros(self.n_stations) if p0 is None else np.asarray(p0, dtype=float)
        key = (float(background_scale), tuple(point), float(vmin), float(vmax),
               float(loading_limit), float(voltage_margin))
        if key in self._linear_cache:
            return {k: v.copy() if isinstance(v, np.ndarray) else v for k, v in self._linear_cache[key].items()}
        if not 0 < vmin + voltage_margin < vmax - voltage_margin or loading_limit <= 0 or voltage_margin < 0:
            raise ValueError('invalid voltage or loading limits')
        baseline = self.evaluate(point, background_scale)
        if not baseline['converged']:
            raise RuntimeError('linearization point did not converge')
        y0 = self._signed_outputs(baseline)
        a = np.empty((len(y0), self.n_stations))
        for j in range(self.n_stations):
            plus = point.copy(); plus[j] += 1.
            upper = self.evaluate(plus, background_scale)
            if point[j] >= 1.:
                minus = point.copy(); minus[j] -= 1.
                lower = self.evaluate(minus, background_scale)
                a[:, j] = (self._signed_outputs(upper) - self._signed_outputs(lower)) / 2.
            else:
                a[:, j] = self._signed_outputs(upper) - y0
            if not upper['converged'] or (point[j] >= 1. and not lower['converged']):
                raise RuntimeError('station finite-difference solve did not converge')
        step = .001
        upper = self.evaluate(point, background_scale + step)
        lower_scale = max(0., background_scale - step)
        lower = self.evaluate(point, lower_scale)
        if not upper['converged'] or not lower['converged']:
            raise RuntimeError('background finite-difference solve did not converge')
        c = (self._signed_outputs(upper) - self._signed_outputs(lower)) / (background_scale + step - lower_scale)
        offset = y0 - a @ point - c * background_scale
        n = len(baseline['voltage_pu'])
        limits = np.r_[np.full(n, -(vmin + voltage_margin)),
                       np.full(n, vmax - voltage_margin),
                       np.full(len(baseline['loading_pu']), loading_limit)]
        names = tuple(f'vmin:{v}' for v in baseline['voltage_names']) + tuple(f'vmax:{v}' for v in baseline['voltage_names']) + tuple(f'loading:{v}' for v in baseline['loading_names'])
        result = dict(A=a, C=c, b=limits-offset, row_names=names, offset=offset, limits=limits,
                      p0=point.copy(), background_scale=float(background_scale),
                      voltage_margin=float(voltage_margin))
        self.evaluate(point, background_scale)
        self._linear_cache[key] = result
        # A long Monte Carlo run must not retain every background operating point.
        if len(self._linear_cache) > 64:
            self._linear_cache.pop(next(iter(self._linear_cache)))
        return {k: v.copy() if isinstance(v, np.ndarray) else v for k, v in result.items()}
