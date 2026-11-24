"""Windows 下 urllib 的 HTTPS 证书上下文辅助。

打包的 Python 3.12 默认只用 OpenSSL 的 ssl.create_default_context(), 在 Windows 上经常拿不到
系统/代理根证书或未缓存在本地的中间证书 (例如访问 api.github.com), 会报:
    SSL: CERTIFICATE_VERIFY_FAILED (unable to get issuer certificate)

这里按顺序尝试:
1. truststore: 走 Windows 系统证书库 (CryptoAPI), 自动处理系统根证书、代理根证书与 AIA 中间证书;
2. certifi: 兜底 Mozilla CA 证书包 (防个别精简版/离线 Windows 系统根证书库过旧);
3. ssl.create_default_context(): 两个依赖都不可用时的标准库兜底。

仅当请求报 SSLCertVerificationError 时才会切到下一个证书上下文重试; 超时、DNS、HTTP 4xx/5xx
等非证书错误直接抛出, 不做多余重试。
"""
import functools
import ssl
from urllib.error import URLError
from urllib.request import urlopen


def _is_cert_verify_error(error):
    if isinstance(error, ssl.SSLCertVerificationError):
        return True
    if isinstance(error, URLError) and isinstance(error.reason, ssl.SSLCertVerificationError):
        return True
    return False


@functools.lru_cache(maxsize=1)
def ssl_contexts():
    """按优先级构建并缓存可用的 SSLContext 列表。"""
    contexts = []
    try:
        import truststore
        contexts.append(truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    except Exception:
        pass
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    if not contexts:
        contexts.append(ssl.create_default_context())
    return tuple(contexts)


def open_url(request, timeout):
    """带 truststore -> certifi 证书回退的 urlopen 封装。"""
    contexts = ssl_contexts()
    for index, context in enumerate(contexts):
        try:
            return urlopen(request, timeout=timeout, context=context)
        except (ssl.SSLCertVerificationError, URLError) as error:
            if index + 1 < len(contexts) and _is_cert_verify_error(error):
                continue
            raise
