# ruff: noqa
class _Any:
    def __init__(self,*a,**k): pass
    def __getattr__(self,n): return _Any()
    def __call__(self,*a,**k): return _Any()
def __getattr__(n):
    if n.startswith("__"): raise AttributeError(n)
    return type(n,(Exception,),{}) if n.endswith("Error") or n.endswith("Exception") else _Any
