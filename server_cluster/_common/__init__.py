"""server_cluster._common — the shared cluster library.

Everything the four area servers have in common: the typed config
(:mod:`.config`), the area/module catalog (:mod:`.catalog`), the child
process manager (:mod:`.process_manager`), the run registry
(:mod:`.run_registry`), the resident-process supervisor
(:mod:`.resident_supervisor`), the FastAPI app factory
(:mod:`.app_factory`) with its routers, and the composition root +
launcher (:mod:`.server`). Area packages only name themselves; all
behavior lives here.
"""
