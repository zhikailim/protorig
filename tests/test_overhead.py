"""
test_overhead.py — result_gui R5/R6: an app's heartbeat reports its writers'
rates, and that reporting never disturbs the demo.

R6.4 measured here, with reporting on and off (switched off inside the test
only: there is no switch for users):
- the reporting work itself, per second, as a share of one CPU;
- an app's reaction time (data in -> data out), which is what a demo shows.
result_gui's own CPU is measured in apps/tooling/result_gui/test_result_gui.py.
"""
import signal
import statistics
import threading
import time

import pytest

dds = pytest.importorskip("rti.connextdds")
from fw import types as T              # noqa: E402
from fw.testing import wait_for        # noqa: E402

SIGNALS = [s for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)) if s]


@pytest.fixture
def make_app(bus):
    """fw.App objects inside this test process, stopped and signal handlers restored after."""
    from fw.app import App
    saved = {s: signal.getsignal(s) for s in SIGNALS}
    made = []

    def make(name="overhead", run=False):
        app = App(name, "test", argv=["--node", "test-node", "--domain", str(bus.domain)])
        t = threading.Thread(target=app.run, daemon=True) if run else None
        made.append((app, t))
        return app, t
    yield make
    for a, t in made:
        a.stop("test over")
        if t is not None and t.is_alive():
            t.join(10)               # DDS closed before the next test: the bus is shared
        elif t is None:
            a.run()                  # never started: run() just closes DDS (stop() came first)
    for s, h in saved.items():
        signal.signal(s, h)


def test_R5_rates_of_scenario_topics_only(make_app):
    app, _ = make_app()
    w = app.writer("Alert")
    app.writer("_sys/AppState")                       # plumbing: never reported
    app._writer_rates()                               # starts the count
    t0 = time.monotonic()
    for i in range(30):
        w.write(T.Alert(source="t", alert_id=str(i % 3)))
    time.sleep(0.5)
    rates = {r.topic: r.per_s for r in app._writer_rates()}
    elapsed = time.monotonic() - t0
    assert set(rates) == {"Alert"}, rates
    assert rates["Alert"] == pytest.approx(30 / elapsed, rel=0.25)
    time.sleep(0.2)
    assert {r.topic: r.per_s for r in app._writer_rates()} == {"Alert": 0.0}, "nothing written since"


def test_R5_rates_reach_the_heartbeat(bus, make_app):
    beats = bus.listen("_sys/NodeStatus")
    app, run = make_app("rated", run=True)
    w = app.writer("Alert")
    app.every(0.05, lambda: w.write(T.Alert(source="t", alert_id="x")))
    run.start()
    wait_for(lambda: any(s.app == "rated" and s.rates and s.rates[0].per_s > 5 for s in beats.all()), 5,
             "a heartbeat with Alert's rate (about 20/s)")


def test_R6_reporting_costs_well_under_1_percent_cpu(make_app):
    """16 writers (the most a heartbeat reports): the work is a counter read each, once a second."""
    app, _ = make_app()
    for _ in range(16):
        app.writer("Alert")
    n = 200
    t0 = time.perf_counter()
    for _ in range(n):
        app._writer_rates()
    per_second = (time.perf_counter() - t0) / n         # it runs once a second
    print(f"\nrate reporting: {per_second * 1e6:.0f} us per second = {per_second * 100:.4f} % of one CPU")
    assert per_second < 0.01, "R6.4: under 1 % CPU"


ECHO = 100          # samples per second sent to the app
BLOCK = 4.0         # seconds measured with reporting on, and again with it off


def test_R6_reaction_time_same_with_reporting_on_and_off(bus, make_app):
    """The app echoes each Temperature as an Alert; the test times each round trip.
    Reporting runs in the app's own loop, so this is where it could delay data."""
    app, run = make_app("echo", run=True)
    out = app.writer("Alert")
    app.on_data(app.reader("Example Temperature"),
                lambda s: out.write(T.Alert(source="echo", alert_id="e", value=s.temperature)))
    real = app._writer_rates
    run.start()

    sent: dict[int, float] = {}
    times: list[tuple[int, float]] = []
    r = dds.DataReader(bus._sub, bus._topic("Alert"), bus.provider.get_topic_datareader_qos("Alert"))
    stop = threading.Event()

    def collect():
        while not stop.is_set():
            for s, info in r.take():
                if info.valid and s.source == "echo":
                    i = int(s.value)
                    if i in sent:
                        times.append((i, time.perf_counter() - sent[i]))
            time.sleep(0.001)
    threading.Thread(target=collect, daemon=True).start()
    w = bus.writer("Example Temperature")
    wait_for(lambda: w.publication_matched_status.current_count > 0, 5, "the echo app's reader")
    wait_for(lambda: r.subscription_matched_status.current_count > 0, 5, "the echo app's writer")

    def block(first: int) -> list[float]:
        start = len(times)
        for i in range(first, first + int(ECHO * BLOCK)):
            sent[i] = time.perf_counter()
            w.write(T.Temperature(temperature=float(i)))
            time.sleep(1 / ECHO)
        time.sleep(0.3)
        return [t for _, t in times[start:]]
    try:
        block(0)                                           # warm-up, not measured
        app._writer_rates = lambda: []                     # reporting off
        off = block(10_000)
        app._writer_rates = real                           # reporting on
        on = block(20_000)
    finally:
        stop.set()
        r.close()

    def ms(xs, q):
        return statistics.quantiles(xs, n=100)[q - 1] * 1000
    print(f"\nreaction time, median / 95th percentile: off {ms(off, 50):.2f} / {ms(off, 95):.2f} ms, "
          f"on {ms(on, 50):.2f} / {ms(on, 95):.2f} ms ({len(off)} and {len(on)} samples)")
    assert len(off) > ECHO * BLOCK * 0.9 and len(on) > ECHO * BLOCK * 0.9, "samples went missing"
    assert ms(on, 50) <= ms(off, 50) + 0.5, "R6.4: no measurable latency (median)"
    assert ms(on, 95) <= ms(off, 95) + 2.0, "R6.4: no measurable latency (95th percentile)"
