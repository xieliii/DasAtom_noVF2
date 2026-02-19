
import sys
import os
import networkx as nx
from qiskit import QuantumCircuit

# Setup environment to import DasAtom modules
sys.path.append(os.getcwd())
try:
    from DasAtom import DasAtom
    from DasAtom_fun import get_embeddings, partition_from_DAG, gates_list_to_QC, get_layer_gates, generate_grid_with_Rb, rx_is_subgraph_iso, extend_graph
    from Enola.route import QuantumRouter
except ImportError as e:
    print(f"Import Error: {e}")
    print("Please run this script from the root directory of the DasAtom project.")
    sys.exit(1)

def test_extend_bug():
    print("=== Testing extend_graph Logic ===")

    # 1. Create a logical graph that requires at least a 3x3 grid (9 nodes)
    # A simple path 0-1-2-...-8
    num_qubits = 9
    edges = [(i, i+1) for i in range(num_qubits - 1)]
    
    # 2. But we initialize with a tiny 2x2 grid (4 nodes)
    initial_arch_size = 2
    Rb = 1.5
    coupling_graph = generate_grid_with_Rb(initial_arch_size, initial_arch_size, Rb)
    
    print(f"Initial Grid: {initial_arch_size}x{initial_arch_size} (Nodes: {len(coupling_graph.nodes)})")
    
    # 3. Simulate Partitioning (Simplified: just one big partition)
    # We manually create a partition that WILL fit in 3x3 but NOT in 2x2
    partition_gates = [edges] 
    
    print(f"Partition Gates: {partition_gates}")
    
    # 4. Run get_embeddings which SHOULD trigger extend_graph
    print("\n>>> Running get_embeddings...")
    try:
        embeddings, extend_pos = get_embeddings(
            partition_gates, 
            coupling_graph, 
            num_qubits, 
            initial_arch_size, 
            Rb
        )
        print(f"Embeddings found: {len(embeddings)}")
        print(f"Extend Positions: {extend_pos}")
        if len(extend_pos) > 0:
            print("SUCCESS: extend_graph was triggered!")
            # Check the coordinates in embedding to see if they exceed (1,1)
            max_coord = 0
            for emb in embeddings:
                for coord in emb:
                    if coord != -1:
                        max_coord = max(max_coord, coord[0], coord[1])
            print(f"Max Coordinate in Embedding: {max_coord}")
        else:
            print("FAILURE: extend_graph was NOT triggered (unexpected).")
            
    except Exception as e:
        print(f"CRASH in get_embeddings: {e}")
        return

    # 5. Now try to run Router with original grid size
    print("\n>>> Running QuantumRouter with ORIGINAL grid size...")
    try:
        # Note: Router expects grid_size list [N, N]
        router = QuantumRouter(
            num_qubits, 
            embeddings, 
            partition_gates, 
            [initial_arch_size, initial_arch_size] # Passing 2x2
        )
        router.run()
        print("Mirror, Mirror on the wall, did it crash?")
        print("Router completed successfully (Surprisingly!)")
        
    except Exception as e:
        print(f"CRASH in QuantumRouter: {e}")
        if "list index out of range" in str(e) or "key" in str(e).lower():
            print("ANALYSIS: This confirms the bug. Coordinates exceeded grid bounds.")

if __name__ == "__main__":
    test_extend_bug()
