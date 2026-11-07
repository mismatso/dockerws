#!/usr/bin/env python3
"""Calcula qué redes de sitios conectar o desconectar del proxy en ejecución.

Lee por stdin las redes actuales del contenedor (una por línea) e imprime
«connect RED» o «disconnect RED». Solo desconecta redes frontales de DockerWS:
dws-*-edge y, en sitios anteriores, dockerws-edge-*.
"""
import sys
from registry import LEGACY_EDGE, edge_network, sites


def managed(name):
    return name.startswith(LEGACY_EDGE) or (name.startswith("dws-") and name.endswith("-edge"))


def plan(desired, current):
    desired, current = set(desired), {n for n in current if n}
    actions = [("connect", n) for n in sorted(desired - current)]
    actions += [("disconnect", n) for n in sorted(current - desired) if managed(n)]
    return actions


if __name__ == "__main__":
    try:
        wanted = [edge_network(row) for row in sites()]
    except (ValueError, KeyError) as exc:
        sys.exit(f"Inventario inválido: {exc}")
    for action, network in plan(wanted, sys.stdin.read().split()):
        print(action, network)
