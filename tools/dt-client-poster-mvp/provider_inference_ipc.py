from contextlib import suppress
from hashlib import sha256
from pathlib import Path
import re
from provider_inference_wire import InferenceWireError, MAX_ASSET, dispatch_inference

CORRELATION = re.compile(r"[0-9a-f]{32}")


def worker_operation(command, provider, method, workspace, key, fixture, spool):
    ident = command.get("id")
    if (not isinstance(ident, str) or not CORRELATION.fullmatch(ident)
            or set(command) != {"op", "id", "operation", "model_id", "payload"} or spool is None):
        raise ValueError("invalid operation frame")
    path = spool / (ident + ".bin")
    created = False
    try:
        result = dispatch_inference(provider, method, workspace, key, command["operation"],
                                    command["model_id"], command["payload"], fixture=fixture)
        media = result.pop("media", None)
        if media is not None:
            with path.open("xb") as stream:
                created = True
                stream.write(media)
            result.update(media_sha256=sha256(media).hexdigest(), media_size=len(media))
        frame = {"operation_id": ident, "result": result}
    except InferenceWireError as exc:
        frame = {"operation_id": ident, "error": exc.code}
    except (OSError, ValueError, TypeError):
        if created:
            with suppress(OSError):
                path.unlink()
        frame = {"operation_id": ident, "error": "worker_failed"}
    return frame


def read_media(spool, ident, result):
    path = spool / (ident + ".bin")
    size = result.pop("media_size", None)
    digest = result.pop("media_sha256", None)
    if type(size) is not int or not 0 < size <= MAX_ASSET or path.resolve() != path:
        raise OSError("invalid operation output")
    with path.open("rb") as stream:
        media = stream.read(MAX_ASSET + 1)
    if len(media) != size or sha256(media).hexdigest() != digest:
        raise OSError("invalid operation output")
    return media
