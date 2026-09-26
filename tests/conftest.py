"""Keep collection out of the fixture repositories.

Several fixtures share test filenames (tests/test_client.py), which is a
pytest collection conflict. Each fixture also carries its own pytest.ini so
that running its suite uses the fixture as rootdir, never this repository.
"""
collect_ignore_glob = ["fixtures/*"]
