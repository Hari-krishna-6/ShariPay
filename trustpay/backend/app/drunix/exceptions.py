class DrunixClientError(RuntimeError):
    pass


class DrunixConflictError(DrunixClientError):
    pass


class DrunixSyncError(DrunixClientError):
    pass
