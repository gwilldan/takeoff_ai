"""
PDF query tools.

Deliberately empty of imports. An eager re-export here would drag every module
in the package into any process that touches one of them — which is how the
legacy bulk-dump tools stayed alive long after nothing called them. Import
`tools.query` directly.
"""
