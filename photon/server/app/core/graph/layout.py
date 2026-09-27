from __future__ import annotations
import math
import random


def compute_layout(
    nodes: list[dict],
    edges: list[dict],
    iterations: int = 100,
    width: float = 1000.0,
    height: float = 1000.0,
) -> list[dict]:
    """
    Assign (x, y) coordinates to nodes using a simple Fruchterman-Reingold
    force-directed layout. Returns a new list of node dicts with 'x' and 'y' added.
    """
    if not nodes:
        return nodes

    n = len(nodes)
    ids = [node["id"] for node in nodes]
    idx = {nid: i for i, nid in enumerate(ids)}

    # Random initial positions
    rng = random.Random(42)
    pos = [[rng.uniform(0, width), rng.uniform(0, height)] for _ in range(n)]

    area = width * height
    k = math.sqrt(area / max(n, 1))

    def repulsion(d: float) -> float:
        return k * k / max(d, 0.01)

    def attraction(d: float) -> float:
        return d * d / k

    temp = width / 10.0
    cooling = temp / (iterations + 1)

    for _ in range(iterations):
        disp = [[0.0, 0.0] for _ in range(n)]

        # Repulsive forces between every pair
        for i in range(n):
            for j in range(i + 1, n):
                dx = pos[i][0] - pos[j][0]
                dy = pos[i][1] - pos[j][1]
                dist = math.sqrt(dx * dx + dy * dy) or 0.01
                force = repulsion(dist)
                fx, fy = (dx / dist) * force, (dy / dist) * force
                disp[i][0] += fx
                disp[i][1] += fy
                disp[j][0] -= fx
                disp[j][1] -= fy

        # Attractive forces along edges
        for edge in edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src not in idx or tgt not in idx:
                continue
            i, j = idx[src], idx[tgt]
            dx = pos[i][0] - pos[j][0]
            dy = pos[i][1] - pos[j][1]
            dist = math.sqrt(dx * dx + dy * dy) or 0.01
            force = attraction(dist)
            fx, fy = (dx / dist) * force, (dy / dist) * force
            disp[i][0] -= fx
            disp[i][1] -= fy
            disp[j][0] += fx
            disp[j][1] += fy

        # Apply displacements with temperature cap
        for i in range(n):
            dx, dy = disp[i]
            dist = math.sqrt(dx * dx + dy * dy) or 0.01
            scale = min(dist, temp) / dist
            pos[i][0] = min(width, max(0, pos[i][0] + dx * scale))
            pos[i][1] = min(height, max(0, pos[i][1] + dy * scale))

        temp = max(temp - cooling, 0.01)

    result = []
    for node, (x, y) in zip(nodes, pos):
        result.append({**node, "x": round(x, 2), "y": round(y, 2)})

    return result
