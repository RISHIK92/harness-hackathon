from __future__ import annotations
from collections import defaultdict


def detect_communities(
    nodes: list[dict],
    edges: list[dict],
) -> list[dict]:
    """
    Assign a 'community' integer to each node using a simple label-propagation
    algorithm. Returns the node list with a 'community' key added.
    """
    if not nodes:
        return nodes

    ids = [n["id"] for n in nodes]
    idx = {nid: i for i, nid in enumerate(ids)}

    # Build adjacency list
    adj: dict[int, list[int]] = defaultdict(list)
    for edge in edges:
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        if src in idx and tgt in idx:
            i, j = idx[src], idx[tgt]
            adj[i].append(j)
            adj[j].append(i)

    # Each node starts in its own community
    labels = list(range(len(ids)))

    for _ in range(10):  # max iterations
        changed = False
        for i in range(len(ids)):
            neighbors = adj[i]
            if not neighbors:
                continue
            # Pick the most common label among neighbours
            freq: dict[int, int] = defaultdict(int)
            for nb in neighbors:
                freq[labels[nb]] += 1
            best = max(freq, key=lambda lbl: (freq[lbl], -lbl))
            if best != labels[i]:
                labels[i] = best
                changed = True
        if not changed:
            break

    # Remap labels to consecutive integers
    unique = sorted(set(labels))
    remap = {old: new for new, old in enumerate(unique)}
    labels = [remap[l] for l in labels]

    result = []
    for node, community in zip(nodes, labels):
        result.append({**node, "community": community})

    return result
