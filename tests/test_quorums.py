import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import requests

_temporary = tempfile.TemporaryDirectory()
os.environ['DATABASE'] = os.path.join(_temporary.name, 'metadata.sqlite')
from storage import metadata
from storage.ring import HashRing


class QuorumTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        metadata.db.close()
        _temporary.cleanup()

    def setUp(self):
        self.body = b'quorum-unit-check'
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.chunk = {'targets': json.dumps(['node1', 'node2', 'node3']),
                      'codec': 'raw:' + self.digest, 'r': 2}

    def response(self, code=200, body=None):
        response = requests.Response()
        response.status_code = code
        response._content = self.body if body is None else body
        return response

    def test_distinct_replica_nodes(self):
        owners = HashRing(['a', 'b', 'c', 'd']).owners(self.digest, 3)
        self.assertEqual(len(set(owners)), 3)
        self.assertEqual(owners[0], HashRing(['a', 'b', 'c', 'd']).owner(self.digest))

    def test_write_tolerates_one_failure(self):
        with patch.object(metadata, 'remote', side_effect=[None, HTTPException(503), None]):
            self.assertEqual(metadata.write_replicas(['node1', 'node2', 'node3'], self.digest, self.body, 2), 2)

    def test_write_rejects_two_failures(self):
        with patch.object(metadata, 'remote', side_effect=[None, HTTPException(503), HTTPException(503)]):
            with self.assertRaises(HTTPException) as error:
                metadata.write_replicas(['node1', 'node2', 'node3'], self.digest, self.body, 2)
            self.assertEqual(error.exception.status_code, 503)

    def test_read_tolerates_one_failure(self):
        with patch.object(metadata.requests, 'get', side_effect=[self.response(), requests.ConnectionError(), self.response()]):
            self.assertEqual(metadata.read_replicas(self.chunk)[0], self.body)

    def test_read_requires_quorum_even_with_valid_content(self):
        with patch.object(metadata.requests, 'get', side_effect=[self.response(), requests.ConnectionError(), requests.ConnectionError()]):
            with self.assertRaises(HTTPException):
                metadata.read_replicas(self.chunk)

    def test_absent_replica_is_response_but_not_content(self):
        with patch.object(metadata.requests, 'get', side_effect=[self.response(), self.response(404), requests.ConnectionError()]):
            self.assertEqual(metadata.read_replicas(self.chunk)[0], self.body)
        with patch.object(metadata.requests, 'get', return_value=self.response(404)):
            with self.assertRaises(HTTPException):
                metadata.read_replicas(self.chunk)

    def test_corruption_is_not_counted_as_valid(self):
        with patch.object(metadata.requests, 'get', side_effect=[self.response(), self.response(body=b'corrupt'), requests.ConnectionError()]):
            with self.assertRaises(HTTPException):
                metadata.read_replicas(self.chunk)


if __name__ == '__main__':
    unittest.main()
