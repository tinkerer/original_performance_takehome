"""
Expand kernel showing group structure for broadcast optimization.

Key insight: Items at the same node can share a broadcast load.
- Round 0: All 256 at node 0 → 1 broadcast
- Round 1: ~128 left (node 1), ~128 right (node 2) → 2 broadcasts
- Round 2: 4 groups → 4 broadcasts
- ...
- Round 10: Wrap to 0 → 1 broadcast again
- Rounds 11-15: Pattern repeats
"""

from problem import Tree, Input, myhash, HASH_STAGES
import random
from collections import defaultdict


def simulate_groupings(seed=123, tree_height=10, batch_size=256, rounds=16):
    """
    Simulate the kernel and track which items are at which nodes.
    Returns grouping info for each round.
    """
    random.seed(seed)
    tree = Tree.generate(tree_height)
    inp = Input.generate(tree, batch_size, rounds)

    n_nodes = len(tree.values)

    round_info = []

    for r in range(rounds):
        # Group items by their current index (node)
        groups = defaultdict(list)
        for i in range(batch_size):
            groups[inp.indices[i]].append(i)

        # Record this round's structure
        info = {
            'round': r,
            'num_unique_nodes': len(groups),
            'groups': dict(groups),  # node -> [item indices]
            'group_sizes': {node: len(items) for node, items in groups.items()},
        }
        round_info.append(info)

        # Execute the round
        for i in range(batch_size):
            idx = inp.indices[i]
            val = inp.values[i]
            val = myhash(val ^ tree.values[idx])
            idx = 2 * idx + (1 if val % 2 == 0 else 2)
            idx = 0 if idx >= n_nodes else idx
            inp.values[i] = val
            inp.indices[i] = idx

    return round_info


def print_round_structure(round_info):
    """Print the grouping structure for each round."""
    print("=" * 70)
    print("ROUND-BY-ROUND GROUP STRUCTURE")
    print("=" * 70)
    print()

    for info in round_info:
        r = info['round']
        n_groups = info['num_unique_nodes']
        groups = info['groups']

        print(f"# Round {r}: {n_groups} unique nodes → {n_groups} broadcast loads")
        print(f"#   (vs 256 scattered loads without grouping)")

        # Show group distribution
        sizes = sorted(info['group_sizes'].values(), reverse=True)
        if len(sizes) <= 8:
            print(f"#   Group sizes: {sizes}")
        else:
            print(f"#   Group sizes: {sizes[:5]} ... {sizes[-3:]} (showing first 5 and last 3)")

        # Show which nodes
        nodes = sorted(groups.keys())
        if len(nodes) <= 8:
            print(f"#   Nodes: {nodes}")
        else:
            print(f"#   Nodes: {nodes[:4]} ... {nodes[-2:]}")
        print()


def generate_grouped_kernel(round_info, output_file="kernel_grouped.py"):
    """Generate kernel code organized by groups."""

    lines = []
    lines.append('"""')
    lines.append("Grouped kernel - items organized by shared node for broadcast loads.")
    lines.append('"""')
    lines.append("")
    lines.append("def kernel_grouped(tree_values, inp_indices, inp_values):")
    lines.append("    n_nodes = len(tree_values)")
    lines.append("    idx = list(inp_indices)")
    lines.append("    val = list(inp_values)")
    lines.append("")

    for info in round_info:
        r = info['round']
        groups = info['groups']
        n_groups = len(groups)

        lines.append(f"    # ===== ROUND {r}: {n_groups} groups =====")

        if n_groups == 1:
            # Single group - one broadcast covers all
            node = list(groups.keys())[0]
            items = groups[node]
            lines.append(f"    # ALL items at node {node} - single broadcast load")
            lines.append(f"    node_val = tree_values[{node}]  # 1 LOAD (broadcast to all)")
            lines.append(f"    for i in {items}:")
            lines.append(f"        val[i] = myhash(val[i] ^ node_val)")
            if r == 10:
                lines.append(f"        idx[i] = 0  # wrap to root")
            else:
                lines.append(f"        idx[i] = 2 * idx[i] + (1 if val[i] % 2 == 0 else 2)")
                lines.append(f"        idx[i] = 0 if idx[i] >= n_nodes else idx[i]")

        elif n_groups <= 8:
            # Small number of groups - explicit broadcast per group
            lines.append(f"    # {n_groups} groups - {n_groups} broadcast loads")
            for node, items in sorted(groups.items()):
                lines.append(f"    # Group at node {node}: {len(items)} items")
                lines.append(f"    node_val_{node} = tree_values[{node}]  # 1 LOAD")
                lines.append(f"    for i in {items}:")
                lines.append(f"        val[i] = myhash(val[i] ^ node_val_{node})")
                lines.append(f"        idx[i] = 2 * idx[i] + (1 if val[i] % 2 == 0 else 2)")
                lines.append(f"        idx[i] = 0 if idx[i] >= n_nodes else idx[i]")

        else:
            # Many groups - switch to indirection
            lines.append(f"    # {n_groups} groups - use indirection (sparse)")
            lines.append(f"    # Load unique node values first")
            lines.append(f"    unique_nodes = {sorted(groups.keys())}")
            lines.append(f"    node_cache = {{n: tree_values[n] for n in unique_nodes}}  # {n_groups} LOADS")
            lines.append(f"    for i in range(256):")
            lines.append(f"        val[i] = myhash(val[i] ^ node_cache[idx[i]])")
            lines.append(f"        idx[i] = 2 * idx[i] + (1 if val[i] % 2 == 0 else 2)")
            lines.append(f"        idx[i] = 0 if idx[i] >= n_nodes else idx[i]")

        lines.append("")

    lines.append("    return idx, val")

    with open(output_file, 'w') as f:
        f.write('\n'.join(lines))

    print(f"Generated {output_file}")


def generate_broadcast_schedule(round_info):
    """Show the optimal load schedule using broadcasts."""

    print("=" * 70)
    print("BROADCAST LOAD SCHEDULE")
    print("=" * 70)
    print()
    print("Round | Unique | Load Strategy")
    print("------|--------|------------------------------------------")

    total_loads = 0
    naive_loads = 0

    for info in round_info:
        r = info['round']
        n_groups = info['num_unique_nodes']

        naive_loads += 256
        total_loads += n_groups

        if n_groups == 1:
            strategy = "1 broadcast → all 256 items"
        elif n_groups <= 32:
            strategy = f"{n_groups} broadcasts → groups"
        else:
            strategy = f"{n_groups} loads (sparse, use indirection)"

        print(f"  {r:2d}  |  {n_groups:3d}   | {strategy}")

    print()
    print(f"Total loads with grouping: {total_loads}")
    print(f"Total loads naive:         {naive_loads}")
    print(f"Load reduction:            {naive_loads / total_loads:.1f}x")


def generate_pseudocode():
    """Generate pseudocode showing the strategy."""

    print()
    print("=" * 70)
    print("PSEUDOCODE: GROUPED KERNEL STRATEGY")
    print("=" * 70)
    print("""
# Round 0: All at node 0
node_val = LOAD tree[0]           # 1 load
BROADCAST node_val to all 256
for each item: hash and branch

# Round 1: Split into left/right
node_val_1 = LOAD tree[1]         # 2 loads
node_val_2 = LOAD tree[2]
BROADCAST node_val_1 to left_group
BROADCAST node_val_2 to right_group
for each item: hash and branch

# Rounds 2-7: Groups double each round
for each group:
    node_val = LOAD tree[group_node]
    BROADCAST to group members
    hash and branch

# Round 8+: Sparse (>128 groups)
# Switch to indirection - load unique nodes into cache
unique_nodes = compute_unique(indices)
for n in unique_nodes:
    cache[n] = LOAD tree[n]       # ~200 loads
for each item:
    node_val = cache[idx[i]]      # no load, from cache
    hash and branch

# Round 10: Wrap - everyone back to 0
node_val = LOAD tree[0]           # 1 load
BROADCAST to all 256
for each item: hash, idx = 0

# Rounds 11-15: Pattern repeats from round 0
""")


if __name__ == "__main__":
    print("Simulating kernel to extract group structure...")
    print()

    round_info = simulate_groupings()

    print_round_structure(round_info)
    generate_broadcast_schedule(round_info)
    generate_pseudocode()

    # Generate the grouped kernel code
    generate_grouped_kernel(round_info)
