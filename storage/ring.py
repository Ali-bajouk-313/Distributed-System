import bisect
import hashlib


class HashRing:
    def __init__(self, nodes, virtual_nodes=128):
        self.points = sorted(
            (self.hash(f'{node}:{i}'), node)
            for node in nodes for i in range(virtual_nodes)
        )
        self.keys = [point for point, _ in self.points]

    @staticmethod
    def hash(value):
        return int(hashlib.sha256(value.encode()).hexdigest(), 16)

    def owner(self, digest):
        return self.owners(digest, 1)[0]

    def owners(self, digest, count):
        if not self.points:
            raise ValueError('No storage nodes configured')
        if not 1 <= count <= len({node for _, node in self.points}):
            raise ValueError('Replica count exceeds distinct storage nodes')
        index = bisect.bisect_left(self.keys, self.hash(digest)) % len(self.points)
        result = []
        for offset in range(len(self.points)):
            node = self.points[(index + offset) % len(self.points)][1]
            if node not in result:
                result.append(node)
            if len(result) == count:
                return result
