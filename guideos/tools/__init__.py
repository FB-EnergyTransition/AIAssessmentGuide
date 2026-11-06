"""MCP tools in the advisory (concept step 5a): registrations of tool servers
per domain, a synchronous client over fastmcp, the guard rails (allowlist,
argument and output checks against the schemas, owner injection, budget)
and the loop in which the model chooses tools and GuideOS executes them.
The model never sees or sets `owner`, and nothing here writes profiles."""
