"""Defining file for all the attribute calculation functions of step 2 of APDUDS

This script requires that `networkx`, `pandas`, `freud` and `numpy` be installed within the Python
environment you are running this script in.

This file contains the following major functions:

    * voronoi_area - Calculates the catchment area for each node using voronoi
    * adjusted_area - Re-calculates the area based on elevation of nearby nodes
    * flow_and_height - Determinte the flow direction and set the node depth
    * flow_amount - Determine the amount of water flow through each conduit
    * diameter_calc - Determine the appropriate diameter for eac conduit
    * uphold_min_depth - Moves all installation levels of pipes to correct location
    * cleaner_and_trimmer - Remove intermediate information and precision from the data
    * attribute_calculations - Runs the entire attribute calculation process
    * tester - Only used for testing purposes
"""

import networkx as nx
import pandas as pd
from freud.box import Box
from freud.locality import Voronoi
import numpy as np
from numpy import random as rnd

def voronoi_area(nodes: pd.DataFrame, edges: pd.DataFrame):
    """Calculates the catchment area for the nodes using voronoi

    Args:
        nodes (pd.DataFrame): The node data of a network

    Returns:
        tuple([pd.DataFrame, Freud.locality.Voronoi]): Node data with added subcatchment area
        values, and freud voronoi object
    """

    nodes = nodes.copy()

    box = Box(Lx=nodes.x.max() * 2, Ly=nodes.y.max() * 2, is2D=True)
    points = np.array([[nodes.x[i], nodes.y[i], 0] for i in range(len(nodes))])

    voro = Voronoi()
    voro.compute((box, points))

    nodes["area"] = voro.volumes

    return nodes, voro

def flow_and_depth(nodes: pd.DataFrame, edges: pd.DataFrame, settings:dict):
    """Determines the direction of flow of the water (using Dijkstra's algorithm) and
    needed installation depth of the nodes based on the given settings.

    Args:
        nodes (DataFrame): The node data of a network
        edges (DataFrame): The conduit data of a network
        settings (dict): Parameters for the network

    Returns:
        tuple[DataFrame, DataFrame]: Node data with added depth and path values,
        and conduit data with "from" and "to" columns corrected
    """

    nodes = nodes.copy()
    edges = edges.copy()

    nodes, edges, graph = intialize(nodes, edges, settings)
    end_points = settings["outfalls"]
    nodes.loc[end_points, "considered"] = True
    # Create a set of all the "to" "from" combos of the conduits for later calculations
    edge_set = [set([edges["from"][i], edges["to"][i]]) for i in range(len(edges))]

    i = 1
    while not nodes["considered"].all():
        # Using the number of connections to sort them will make leaf nodes be considered first,
        # which has a larger change to include more nodes in one dijkstra run
        leaf_nodes = nodes.index[nodes.connections == i].tolist()

        for node in leaf_nodes:
            if not nodes.at[node, "considered"]:
                path = determine_path(graph, node, end_points)
                nodes = set_paths(nodes, path)
                nodes = set_depth(nodes, edges, path, settings["min_slope"], edge_set)

                nodes.loc[path, "considered"] = True
        i += 1

    if "max_slope" in settings:
        nodes = uphold_max_slope(nodes, edges, edge_set, settings["max_slope"])

    edges = reset_direction(nodes, edges)
    return nodes, edges


def intialize(nodes: pd.DataFrame, edges: pd.DataFrame, settings: dict):
    """Add the needed columns to the node and edge datasets to facilitate the operations
    of later functions. Also creates a networkx graph for the dijkstra calculations

    Args:
        nodes (DataFrame): The node data of a network
        edges (DataFrame): The conduit data of a network
        settings (dict): Parameters for the network

    Returns:
        tuple[DataFrame, DataFrame, Graph]: The node and edge datasets with the needed columns
        added, and a networkx graph of the network
    """

    nodes["considered"] = False
    nodes["depth"] = nodes["elevation"] - settings["min_depth"]
    nodes["role"] = "node"
    nodes["path"] = None

    # Some more complex pandas operations are needed to get the connection numbers in a few lines
    ruined_edges = edges.copy()
    edges_melted = ruined_edges[["from", "to"]].melt(var_name='columns', value_name='index')
    edges_melted["index"] = edges_melted["index"].astype(int)
    nodes["connections"] = edges_melted["index"].value_counts().sort_index()

    graph = nx.Graph()
    graph.add_nodes_from(list(nodes.index.values))

    # Add weights to each conduit based on elevation change
    for _, edge in edges.iterrows():
        slope = (nodes.at[int(edge["from"]), "elevation"] - nodes.at[int(edge["to"]), "elevation"])  / edge["length"]
        if  slope >= 0:
             graph.add_edge(edge["from"], edge["to"], weight = 1 * abs(slope) * edge["length"])
        else:
            graph.add_edge(edge["from"], edge["to"], weight = 10 * abs(slope) * edge["length"] )

    return nodes, edges, graph


def determine_path(graph: nx.Graph, start: int, ends: list[int]):
    """Determines the shortest path from a certain point to another point on a networkx graph
    using Dijkstra's shortes path algorithm

    Args:
        graph (Graph): A NetworkX Graph object of the network
        start (int): The index of the starting node
        end (int): The index of the end node

    Returns:
        list[int]: The indicies of the nodes which the shortes path passes through
    """

    shortest_length = np.inf
    best_path = []

    for end_point in ends:
        length, path = nx.single_source_dijkstra(graph, start, target=end_point)

        if length < shortest_length:
            best_path = path
            shortest_length = length

    # Generator expression is needed to remove the .0 that is added by networkx' dijkstra
    return [int(x) for x in best_path]


def set_paths(nodes: pd.DataFrame, path: list):
    """Determine the path to the outfall for each node, and add this to the node data

    Args:
        nodes (DataFrame): The node data for a network
        path (list[int]): The indicies of the nodes which a path passes through

    Returns:
        DataFrame: Node data with the relevant path values updated
    """

    for i, node in enumerate(path):

        if not nodes.loc[node, "path"]:
            nodes.at[node, "path"] = path[i:]

    return nodes


def set_depth(nodes: pd.DataFrame, edges: pd.DataFrame,
              path: list, min_slope: float, edge_set: list[set[int]]):
    """Set the depth of the nodes along a certain route using the given minimum slope.

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        path (list): All the indicies of the nodes which the path passes through
        min_slope (float): The value for the minimum slope [m/m]

    Returns:
        DataFrame: The node data with the relevant depth values updated
    """

    for i in range(len(path) - 1):
        from_node = path[i]
        to_node = path[i+1]

        from_depth = nodes.at[from_node, "depth"]
        # Use the edge set to get the conduit index
        length = edges.at[edge_set.index(set([from_node, to_node])), "length"]
        new_to_depth = from_depth - min_slope * length

        # Only update the depth if the new depth is deeper than the current depth
        if new_to_depth < nodes.at[to_node, "depth"]:
            nodes.at[to_node, "depth"] = new_to_depth

    return nodes

def uphold_max_slope(nodes: pd.DataFrame, edges: pd.DataFrame,\
                     edge_set: list[set[int]], max_slope: float):
    """Checks if the conduits uphold the max slope rule, and alters/lowers the relevant nodes
    when this isn't the case

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        edge_set (list[set[int]]): A list of sets of all the "from" "to" node combos
        of the conduits
        max_slope (float): The value of the maximum slope [m/m]

    Returns:
        DataFrame: The node data with the depth value updated were needed
    """

    for _, node in nodes.iterrows():
        path = node.path

        for i in range(len(path)-1):
            lower_node = path[-1-i]
            higher_node = path[-2-i]
            length = edges.at[edge_set.index(set([lower_node, higher_node])), "length"]

            if abs(nodes.at[lower_node, "depth"] - nodes.at[higher_node, "depth"])\
                 / length > max_slope:
                nodes.at[higher_node, "depth"] = nodes.at[lower_node, "depth"] + length * max_slope

    return nodes


def reset_direction(nodes: pd.DataFrame, edges: pd.DataFrame):
    """Flips the "from" and "to" columns for all conduits where needed if depth is reversed

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network

    Returns:
        DataFrame: Conduit data with the "from" "to" order flipped were needed
    """

    for i, edge in edges.iterrows():
        if nodes.at[edge["from"], "depth"] < nodes.at[edge["to"], "depth"]:
            edges.at[i, "from"], edges.at[i, "to"] = edge["to"], edge["from"]

    return edges

def adjusted_area(nodes: pd.DataFrame, edges: pd.DataFrame):
    """Re-calculate the areas of all nodes based on elevation of nearby nodes.

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        settings (dict): Network parameters

    Returns:
        tuple[DataFrame, DataFrame]: Node and conduit data with the adjusted area
    """

    for i, _ in nodes.iterrows():
        length_elevation_above, length_above, length_elevation_below, length_below = 0, 0, 0, 0 
        for _, edge in edges[edges["from"] == i].iterrows():
            length = edge["length"]
            elevation  = nodes.at[int(edge["to"]), "elevation"]
            if elevation - nodes.at[i, "elevation"] > 0:
                length_elevation_above += length * elevation
                length_above += length
            else: 
                length_elevation_below += length * elevation
                length_below += length
        try:
            eq_nodes_above = length_elevation_above / length_above
        except ZeroDivisionError:
            eq_nodes_above = 0
        try:
            eq_nodes_below = length_elevation_below / length_below
        except ZeroDivisionError:
            eq_nodes_below = 0

        if nodes.at[i, "elevation"] != 0:
            factor = (np.exp((eq_nodes_above - eq_nodes_below) / nodes.at[i, "elevation"]))**0.25
        else:
            factor = (np.exp((eq_nodes_above - eq_nodes_below) / elevation))**0.25
        nodes.at[i, "area"] = nodes.at[i, "area"] * factor

    return nodes, edges


def flow_amount(nodes: pd.DataFrame, edges: pd.DataFrame, settings: dict):
    """Calculate the amount of flow through each conduit, and convert peak rain value to m/s.

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        settings (dict): Network parameters

    Returns:
        tuple[DataFrame, DataFrame]: Node and conduit data with the inflow and flow
        values added
    """

    nodes = nodes.copy()
    edges = edges.copy()

    nodes["inflow"] = nodes["area"] * (settings["peak_rain"] / (3.6e6))\
         * (settings["perc_inp"] / 100)
    edges["flow"] = 0
    edge_set = [set([edges["from"][i], edges["to"][i]]) for i in range(len(edges))]

    for _, node in nodes.iterrows():
        path = node["path"]

        for j in range(len(path)-1):
            edge = set([path[j], path[j+1]])
            edges.at[edge_set.index(edge), "flow"] += node["inflow"]

    return nodes, edges


def diameter_calc(edges: pd.DataFrame, diam_list: list[float]):
    """Determine the needed diameter for the conduits from a given list of diameters using the
    calculate flow amount

    Args:
        edges (DataFrame): The conduit data for a network
        diam_list (list[float]): List of the different usable diameter sizes for the
        conduits [m]

    Returns:
        DataFrame: Conduit data with diameter values added
    """

    edges["diameter"] = None

    for i, edge in edges.iterrows():
        precise_diam = np.sqrt(4 * edge["flow"] / np.pi)

        if edge["flow"] == 0:
            edges.at[i, "diameter"] = 0

        # Special case if the precise diameter is larger than the largest given diameter
        elif precise_diam > diam_list[-1]:
            edges.at[i, "diameter"] = diam_list[-1]
            print(f"WARNING: Conduit between node {int(edge['from'])} and {int(edge['to'])} \
requires a larger diameter than is available ({round(precise_diam, 3)} m). \
Capped to {diam_list[-1]}")

        else:
            for size in diam_list:
                if size - precise_diam > 0:
                    edges.at[i, "diameter"] = size

                    break

    return edges

def uphold_min_depth(nodes: pd.DataFrame, edges: pd.DataFrame, settings: dict):
    """Move all pipes lower so that they follow the set minimum depth.

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        settings (dict): Network parameters

    Returns:
        tuple[DataFrame, DataFrame]: Node and conduit data with the updated node depth
    """
    
    for i, node in nodes.iterrows():
        try: 
            nodes.at[i, "install_depth"] = float(node["depth"] - edges["diameter"][edges["from"].values == i].values.max())
        except ValueError: #Raised if outflow or overflow node is reached
            nodes.at[i, "install_depth"] = float(node["depth"] - edges["diameter"][edges["to"].values == i].values.max())
            pass

    return nodes, edges


def cleaner_and_trimmer(nodes: pd.DataFrame, edges: pd.DataFrame):
    """Remove the columns from the node and conduit dataframes which were only needed for the
    attribute calculations. Also round off the calculated values to realistic presicions

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network

    Returns:
        tuple[DataFrame, DataFrame]: Cleaned up nodes and conduit data
    """

    nodes = nodes.drop(columns=["considered", "path", "connections"])

    # Special condition if data was obtained from a csv (only for testing purposes)
    if "Unnamed: 0" in nodes.keys():
        nodes = nodes.drop(columns=["Unnamed: 0"])
        edges = edges.drop(columns=["Unnamed: 0"])

    # cm precision for x, y and depth
    # m^2 precision for area
    # L precision for inflow
    nodes.x = nodes.x.round(decimals=2)
    nodes.y = nodes.y.round(decimals=2)
    nodes.area = nodes.area.round(decimals=0)
    nodes.depth = nodes.depth.round(decimals=2)
    nodes.inflow = nodes.inflow.round(decimals=3)
    nodes.install_depth = nodes.install_depth.round(decimals=4)

    # cm precision for length
    # L precision for flow
    edges.length = edges.length.round(decimals=2)
    edges.flow = edges.flow.round(decimals=3)

    # Drop the conduits with 0 flow
    edges = edges[edges.flow != 0]
    edges = edges.reset_index(drop=True)

    return nodes, edges


def add_outfalls(nodes: pd.DataFrame, edges: pd.DataFrame, settings: dict):
    """Add extra nodes for the selected outfall and overflow nodes. Connect them up with new
    conduits

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        settings (dict): Parameters for the network

    Returns:
        tuple[DataFrame, DataFrame]: The node and conduit data with extra nodes and conduits
        for the outfalls and overflows
    """

    for outfall in settings["outfalls"]:
        new_index = len(nodes)
        nodes.loc[new_index] = [nodes.at[outfall, "x"] + 5,
                                nodes.at[outfall, "y"] + 5,
                                nodes.at[outfall, "elevation"],
                                0,
                                nodes.at[outfall, "depth"],
                                "outfall",
                                0,
                                nodes.at[outfall, "install_depth"]]

        edges.loc[len(edges)] = [outfall,
                                 new_index,
                                 1,
                                 0,
                                 settings["diam_list"][-1]]

    for overflow in settings["overflows"]:
        new_index = len(nodes)
        nodes.loc[new_index] = [nodes.at[overflow, "x"] + 5,
                                nodes.at[overflow, "y"] + 5,
                                nodes.at[overflow, "elevation"],
                                0,
                                nodes.at[overflow, "depth"],
                                "overflow",
                                0,
                                nodes.at[overflow, "install_depth"]]

        edges.loc[len(edges)] = [new_index,
                                 overflow,
                                 1,
                                 0,
                                 settings["diam_list"][-1]]

    return nodes, edges


def loop(nodes: pd.DataFrame, edges: pd.DataFrame, settings: dict, type: str):
    """Runs the main attibute calculations loop for a given network

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        settings (dict): Parameters for the network

    Returns:
        tuple[DataFrame, DataFrame]: The node and conduit data which the attribute values updated
    """

    nodes, edges = flow_and_depth(nodes, edges, settings)
    if type == "outfall":
        nodes, edges = adjusted_area(nodes, edges)
    nodes, edges = flow_amount(nodes, edges, settings)
    edges = diameter_calc(edges, settings["diam_list"]) 
    nodes, edges = uphold_min_depth(nodes, edges, settings)

    return nodes, edges


def attribute_calculation(nodes: pd.DataFrame, edges: pd.DataFrame, settings: dict):
    """Does the complete attribute calculation step for a given network

    Args:
        nodes (DataFrame): The node data for a network
        edges (DataFrame): The conduit data for a network
        settings (dict): Parameters for the network

    Returns:
        tuple[DataFrame, DataFrame]: The node and conduit data with newly added and updated
        attribute values
    """
    nodes, voro = voronoi_area(nodes, nodes)
    area = nodes.area.sum()

    nodes_copy = nodes.copy()
    edges_copy = edges.copy()

    nodes, edges = loop(nodes, edges, settings, "outfall")

    loop_setting = settings.copy()
    for overflow in settings["overflows"]:
        loop_setting["outfalls"] = [overflow]
        _, loop_edges = loop(nodes_copy, edges_copy, loop_setting, "overflow")

        for i in range(len(edges)):
            if edges.at[i, "diameter"] < loop_edges.at[i, "diameter"]:
                edges.at[i, "diameter"] = loop_edges.at[i, "diameter"]
                edges.at[i, "flow"] = loop_edges.at[i, "flow"]

    nodes, edges = cleaner_and_trimmer(nodes, edges)
    nodes, edges = add_outfalls(nodes, edges, settings)

    return nodes, edges, voro

def tester():
    """Only used for testing purposes
    """
    print("attribute_calculator script has run")


if __name__ == "__main__":
    tester()
