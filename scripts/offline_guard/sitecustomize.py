"""The offline runner injects this into its Python children only."""
import sys


def block_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise PermissionError("D&T offline pipeline: network access is disabled")


sys.addaudithook(block_network)
