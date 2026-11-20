/**
 * Regenerate tests/geo/fixtures/map_matching_parity.json from tempest-react-sdk.
 *
 * The Python port of matchToRoads (tempest_fastapi_sdk/geo/map_matching.py)
 * is checked against what the TypeScript original answers for the same graph
 * and the same fixes. This script builds the scenarios (graph + traces, with
 * a seeded generator so the input is reproducible), runs the TypeScript
 * matcher on them and writes inputs and outputs side by side.
 *
 * Timed scenarios (tempest-react-sdk#678): a fix may carry `timestamp` (epoch
 * ms), and a scenario may bring its own `graph` and `options`. The detour bound
 * of a timed transition grows by `detourPerSecondM` per second, capped at
 * `maxIntervalS`. Each timed scenario states what it must show (`check`); the
 * script refuses to write a fixture whose TypeScript answer contradicts it.
 * To keep the file small, a scenario that re-times an untimed trace names it
 * (`fixesFrom`) and lists only the times (`timestamps`, epoch ms or null), and
 * one whose answer must equal another's names it (`expectedFrom`) instead of
 * repeating it — the script has already checked the two are equal.
 *
 * Usage:
 *   node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist
 *
 * The dist must be built (npm run build in tempest-react-sdk, with #678).
 */
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const dist = process.argv[2];
if (!dist) {
    console.error("usage: node scripts/map_matching_parity_fixture.mjs <react-sdk>/dist");
    process.exit(2);
}
const { createRoadGraph } = await import(pathToFileURL(resolve(dist, "geo/road-graph.js")).href);
const { matchToRoads } = await import(pathToFileURL(resolve(dist, "geo/map-matching.js")).href);

const BASE = { latitude: -5.09, longitude: -42.8 };
const M_PER_DEG = 6371008.8 * (Math.PI / 180);

/**
 * A coordinate x metres east and y metres north of BASE.
 *
 * @param {number} x - Metres east.
 * @param {number} y - Metres north.
 * @returns {{latitude: number, longitude: number}} The coordinate.
 */
function at(x, y) {
    return {
        latitude: BASE.latitude + y / M_PER_DEG,
        longitude: BASE.longitude + x / (M_PER_DEG * Math.cos((BASE.latitude * Math.PI) / 180)),
    };
}

/**
 * Seeded uniform generator (mulberry32).
 *
 * @param {number} seed - Seed.
 * @returns {() => number} Uniform in [0, 1).
 */
function rng(seed) {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/**
 * Gaussian sample by Box–Muller.
 *
 * @param {() => number} rand - Uniform source.
 * @returns {number} Standard normal sample.
 */
function gauss(rand) {
    const u = Math.max(rand(), 1e-12);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rand());
}

/**
 * A 6×6 grid of 120 m blocks, two-way, with one one-way avenue (y = 240,
 * eastbound only), a parallel side street 18 m north of y = 0 joined only at
 * its ends, a diagonal, and a one-way road 2.3 km east that nothing
 * connects to (a jump there breaks the chain).
 *
 * @returns {{nodes: object[], edges: object[]}} The graph input.
 */
function city() {
    const nodes = [];
    const edges = [];
    const id = (i, j) => i * 7 + j;
    for (let i = 0; i <= 6; i += 1) for (let j = 0; j <= 6; j += 1) nodes.push(at(i * 120, j * 120));
    for (let i = 0; i <= 6; i += 1) {
        for (let j = 0; j < 6; j += 1) {
            edges.push({ from: id(i, j), to: id(i, j + 1), twoWay: true, id: `ns-${i}-${j}` });
        }
    }
    for (let j = 0; j <= 6; j += 1) {
        for (let i = 0; i < 6; i += 1) {
            const oneWay = j === 2;
            edges.push({ from: id(i, j), to: id(i + 1, j), twoWay: !oneWay, id: `ew-${i}-${j}` });
        }
    }
    const side = nodes.length;
    for (let k = 0; k <= 3; k += 1) nodes.push(at(120 + k * 120, 18));
    for (let k = 0; k < 3; k += 1) edges.push({ from: side + k, to: side + k + 1, twoWay: true, id: `side-${k}` });
    edges.push({ from: id(1, 0), to: side, twoWay: true, id: "side-w" });
    edges.push({ from: id(4, 0), to: side + 3, twoWay: true, id: "side-e" });
    edges.push({ from: id(0, 6), to: id(2, 4), twoWay: true, id: "diag" });
    const island = nodes.length;
    nodes.push(at(3000, 0), at(3300, 0), at(3600, 0));
    edges.push({ from: island, to: island + 1, id: "island-0" });
    edges.push({ from: island + 1, to: island + 2, id: "island-1" });
    return { nodes, edges };
}

/**
 * Sample a path through plane waypoints at a speed, 1 Hz, with noise.
 *
 * @param {number[][]} waypoints - Plane points [x, y], metres.
 * @param {number} speed - Metres per second.
 * @param {number} sigma - GPS noise, metres.
 * @param {() => number} rand - Uniform source.
 * @returns {{latitude: number, longitude: number}[]} The fixes.
 */
function drive(waypoints, speed, sigma, rand) {
    const fixes = [];
    for (let w = 0; w < waypoints.length - 1; w += 1) {
        const [ax, ay] = waypoints[w];
        const [bx, by] = waypoints[w + 1];
        const len = Math.hypot(bx - ax, by - ay);
        const steps = Math.max(1, Math.round(len / speed));
        for (let s = 0; s < steps; s += 1) {
            const t = s / steps;
            fixes.push(at(ax + (bx - ax) * t + gauss(rand) * sigma, ay + (by - ay) * t + gauss(rand) * sigma));
        }
    }
    const [lx, ly] = waypoints[waypoints.length - 1];
    fixes.push(at(lx, ly));
    return fixes;
}

const graphInput = city();
const graph = createRoadGraph(graphInput);
const rand = rng(667);
const traces = {
    grid_tour: drive(
        [
            [0, 0],
            [0, 360],
            [480, 360],
            [480, 120],
            [720, 120],
        ],
        11,
        6,
        rand,
    ),
    one_way_avenue: drive(
        [
            [0, 240],
            [720, 240],
        ],
        13,
        5,
        rand,
    ),
    side_street: drive(
        [
            [0, 3],
            [120, 3],
            [120, 18],
            [480, 18],
            [480, 3],
            [720, 3],
        ],
        9,
        4,
        rand,
    ),
    diagonal_and_stop: [
        ...drive(
            [
                [0, 720],
                [240, 480],
            ],
            10,
            5,
            rand,
        ),
        ...Array.from({ length: 6 }, () => at(240 + gauss(rand) * 3, 480 + gauss(rand) * 3)),
        ...drive(
            [
                [240, 480],
                [240, 0],
            ],
            12,
            6,
            rand,
        ),
    ],
    gap_and_far_fix: [
        ...drive(
            [
                [0, 600],
                [240, 600],
            ],
            10,
            5,
            rand,
        ),
        at(240, 1500),
        ...drive(
            [
                [720, 0],
                [720, 360],
            ],
            10,
            5,
            rand,
        ),
    ],
    jump_to_island: [
        ...drive(
            [
                [0, 0],
                [360, 0],
            ],
            15,
            5,
            rand,
        ),
        ...drive(
            [
                [3020, 2],
                [3580, 2],
            ],
            15,
            5,
            rand,
        ),
    ],
};

/**
 * The output of matchToRoads in the fixture's shape.
 *
 * @param {object} result - What matchToRoads returned.
 * @returns {object} Matches, routes and counters, edges by index.
 */
function expectedOf(result) {
    return {
        matches: result.matches.map((m) =>
            m === null
                ? null
                : {
                      edgeIndex: m.edgeIndex,
                      reversed: m.reversed,
                      point: m.point,
                      distanceM: m.distanceM,
                      offsetM: m.offsetM,
                      inferred: m.inferred,
                  },
        ),
        routes: result.routes.map((r) => ({
            edges: r.edges.map((e) => [e.edgeIndex, e.reversed]),
            firstFix: r.firstFix,
            lastFix: r.lastFix,
        })),
        breaks: result.breaks,
        dropped: result.dropped,
        unmatched: result.unmatched,
    };
}

const scenarios = {};
for (const [name, fixes] of Object.entries(traces)) {
    scenarios[name] = { fixes, expected: expectedOf(matchToRoads(fixes, graph)) };
}

/**
 * A one-way block round which a vehicle must drive to get from one side to the
 * other: up a west leg (x = 0), along a top (y = height) and down an east leg
 * (x = 150). From 10 m up the west leg to 10 m up the east leg is
 * `2 * (height - 10) + 150` metres along the network, 150 in a straight line.
 *
 * @param {number} height - Length of the legs, metres.
 * @returns {{nodes: object[], edges: object[]}} The graph input.
 */
function block(height) {
    return {
        nodes: [at(0, 0), at(0, height), at(150, height), at(150, 0)],
        edges: [
            { from: 0, to: 1, id: "west" },
            { from: 1, to: 2, id: "top" },
            { from: 2, to: 3, id: "east" },
        ],
    };
}

const T0 = 1_700_000_000_000;

/**
 * Stamp fixes with times, in seconds after T0; `null` leaves a fix untimed.
 *
 * @param {object[]} fixes - The fixes.
 * @param {(number | null)[]} seconds - Offset of each, or `null`.
 * @returns {object[]} New fixes, `timestamp` in epoch ms where given.
 */
function stamp(fixes, seconds) {
    return fixes.map((f, i) => (seconds[i] === null ? { ...f } : { ...f, timestamp: T0 + seconds[i] * 1000 }));
}

const ACROSS = [at(0, 10), at(150, 10)];
const NEAR_ONLY = { radiusM: 50 };
const FOUR = [at(0, 10), at(0, 30), at(150, 30), at(150, 10)];
const timed = {
    block80_untimed: { graph: block(80), fixes: ACROSS, options: NEAR_ONLY, check: { breaks: 1 } },
    block80_30s: {
        graph: block(80),
        fixes: stamp(ACROSS, [0, 30]),
        options: NEAR_ONLY,
        check: { breaks: 0, ids: ["west", "east"], route: ["west", "top", "east"] },
    },
    block80_5s: { graph: block(80), fixes: stamp(ACROSS, [0, 5]), options: NEAR_ONLY, check: { breaks: 1 } },
    block80_30s_rate0: {
        graph: block(80),
        fixes: stamp(ACROSS, [0, 30]),
        options: { ...NEAR_ONLY, detourPerSecondM: 0 },
        check: { breaks: 1, sameAs: "block80_untimed" },
    },
    block80_repeated_clock: {
        graph: block(80),
        fixes: stamp(ACROSS, [0, 0]),
        options: NEAR_ONLY,
        check: { breaks: 1, sameAs: "block80_untimed" },
    },
    block80_clock_back: {
        graph: block(80),
        fixes: stamp(ACROSS, [0, -30]),
        options: NEAR_ONLY,
        check: { breaks: 1, sameAs: "block80_untimed" },
    },
    block80_only_first_timed: {
        graph: block(80),
        fixes: stamp(ACROSS, [0, null]),
        options: NEAR_ONLY,
        check: { breaks: 1, sameAs: "block80_untimed" },
    },
    block80_only_second_timed: {
        graph: block(80),
        fixes: stamp(ACROSS, [null, 30]),
        options: NEAR_ONLY,
        check: { breaks: 1, sameAs: "block80_untimed" },
    },
    block130_600s: { graph: block(130), fixes: stamp(ACROSS, [0, 600]), options: NEAR_ONLY, check: { breaks: 1 } },
    block130_600s_cap120: {
        graph: block(130),
        fixes: stamp(ACROSS, [0, 600]),
        options: { ...NEAR_ONLY, maxIntervalS: 120 },
        check: { breaks: 0 },
    },
    block130_600s_rate3: {
        graph: block(130),
        fixes: stamp(ACROSS, [0, 600]),
        options: { ...NEAR_ONLY, detourPerSecondM: 3 },
        check: { breaks: 0 },
    },
    block100_four_untimed: {
        graph: block(100),
        fixes: FOUR,
        options: {},
        check: { ids: ["west", "top", "east", "east"] },
    },
    block100_four_timed: {
        graph: block(100),
        fixes: stamp(FOUR, [0, 10, 40, 50]),
        options: {},
        check: { breaks: 0, ids: ["west", "west", "east", "east"] },
    },
};
for (const [name, fixes] of Object.entries(traces)) {
    const seconds = fixes.map((_, i) => i);
    timed[`${name}_1hz`] = { fixesFrom: name, fixes: stamp(fixes, seconds), options: {}, check: {} };
    timed[`${name}_1hz_rate0`] = {
        fixesFrom: name,
        fixes: stamp(fixes, seconds),
        options: { detourPerSecondM: 0 },
        check: { sameAs: name },
    };
}

for (const [name, scenario] of Object.entries(timed)) {
    const own = scenario.graph === undefined ? graph : createRoadGraph(scenario.graph);
    const edgeIds = (scenario.graph ?? graphInput).edges.map((e) => e.id);
    const expected = expectedOf(matchToRoads(scenario.fixes, own, scenario.options));
    const { check } = scenario;
    const fail = (what) => {
        throw new Error(`${name}: ${what} (${JSON.stringify(expected)})`);
    };
    if (check.breaks !== undefined && expected.breaks !== check.breaks) fail(`breaks != ${check.breaks}`);
    if (check.ids !== undefined) {
        const got = expected.matches.map((m) => (m === null ? null : edgeIds[m.edgeIndex]));
        if (JSON.stringify(got) !== JSON.stringify(check.ids)) fail(`ids ${JSON.stringify(got)}`);
    }
    if (check.route !== undefined) {
        const got = expected.routes.map((r) => r.edges.map(([i]) => edgeIds[i]));
        if (JSON.stringify(got) !== JSON.stringify([check.route])) fail(`route ${JSON.stringify(got)}`);
    }
    if (check.sameAs !== undefined) {
        const other = scenarios[check.sameAs]?.expected;
        if (JSON.stringify(other) !== JSON.stringify(expected)) fail(`differs from ${check.sameAs}`);
    }
    const entry = { ...(scenario.graph === undefined ? {} : { graph: scenario.graph }), options: scenario.options };
    if (scenario.fixesFrom === undefined) entry.fixes = scenario.fixes;
    else {
        entry.fixesFrom = scenario.fixesFrom;
        entry.timestamps = scenario.fixes.map((f) => f.timestamp ?? null);
    }
    if (check.sameAs === undefined) entry.expected = expected;
    else entry.expectedFrom = check.sameAs;
    scenarios[name] = entry;
}

const out = resolve("tests/geo/fixtures/map_matching_parity.json");
writeFileSync(
    out,
    `${JSON.stringify({ source: "tempest-react-sdk matchToRoads (main 3576adc, PR #678 included)", graph: graphInput, scenarios }, null, 1)}\n`,
);
console.log(`wrote ${out}`);
