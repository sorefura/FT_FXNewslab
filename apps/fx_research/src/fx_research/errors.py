class NewsCollectionError(RuntimeError):
    pass


class SourceRetrievalError(NewsCollectionError):
    pass


class SourceStructureError(NewsCollectionError):
    pass


class DetailContentError(NewsCollectionError):
    pass


class FeatureProductionError(RuntimeError):
    pass


class MarketDataError(RuntimeError):
    pass
