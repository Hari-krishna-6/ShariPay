class DrunixClientError(RuntimeError):
    pass


class DrunixSyncError(DrunixClientError):
    pass
