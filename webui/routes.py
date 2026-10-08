"""Explicit SPA routes; unknown API and asset paths retain 404 responses."""
PAGE_ROUTES = frozenset({"/", "/jobs", "/experiments", "/comparisons", "/models", "/guide"})

def is_page_route(path):
    return (path.removesuffix("/") or "/") in PAGE_ROUTES
