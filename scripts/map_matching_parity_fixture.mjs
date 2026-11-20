/**
 * Regenerate tests/geo/fixtures/map_matching_parity.json from tempest-react-sdk.
 *
 * The Python port of matchToRoads (tempest_fastapi_sdk/geo/map_matching.py)
 * is checked against what the TypeScript original answers for the same graph
 * and the same fixes. This script builds the scenarios (graph + traces, with
 * a seeded generator so the input is reproducible), runs the TypeScript
 * matcher on them and writes inputs and outputs side by side.
 *
 * Usage:
 *   node scripts/map_matching_parity_fixture.mjs <tempest-react-sdk>/dist
 *
 * The dist must be built (npm run build in tempest-react-sdk).
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

const scenarios = {};
for (const [name, fixes] of Object.entries(traces)) {
    const result = matchToRoads(fixes, graph);
    scenarios[name] = {
        fixes,
        expected: {
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
        },
    };
}

const out = resolve("tests/geo/fixtures/map_matching_parity.json");
writeFileSync(
    out,
    `${JSON.stringify({ source: "tempest-react-sdk matchToRoads", graph: graphInput, scenarios }, null, 1)}\n`,
);
console.log(`wrote ${out}`);
