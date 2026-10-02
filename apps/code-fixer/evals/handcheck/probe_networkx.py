"""networkx-8895 probe: random small graphs (some of the issue's shape: G2 plus isolated nodes),
Graph and DiGraph. Per trial: is_isomorphic, every isomorphism, the subgraph-isomorphism count."""
import json, random, itertools
import networkx as nx
from networkx.algorithms.isomorphism import GraphMatcher, DiGraphMatcher
rng = random.Random(0)
out = []
def canon(ms): return sorted(sorted(m.items()) for m in ms)
for trial in range(400):
    directed = trial % 2 == 1
    n1, n2 = rng.randint(1, 6), rng.randint(1, 6)
    if trial % 3 == 0: n2 = n1           # same order, often different degrees
    G1 = nx.gnp_random_graph(n1, rng.random(), seed=rng.randint(0, 10**6), directed=directed)
    G2 = nx.gnp_random_graph(n2, rng.random(), seed=rng.randint(0, 10**6), directed=directed)
    if trial % 5 == 0:                    # G1 = G2 plus isolated nodes: the issue's shape
        G1 = G2.copy(); G1.add_nodes_from(range(100, 100 + rng.randint(1, 3)))
    M = DiGraphMatcher if directed else GraphMatcher
    out.append([trial, M(G1, G2).is_isomorphic(), canon(M(G1, G2).isomorphisms_iter()),
                len(list(M(G1, G2).subgraph_isomorphisms_iter()))])
print(json.dumps(out))
