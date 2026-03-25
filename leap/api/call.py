"""POST /exp/{experiment}/call — RPC endpoint."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from leap.api.deps import get_experiment_info
from leap.core import rpc
from leap.core.experiment import ExperimentInfo

logger = logging.getLogger(__name__)

router = APIRouter()


class CallRequest(BaseModel):
    student_id: str
    func_name: str
    args: list | None = None
    kwargs: dict | None = None
    trial: str | None = None


@router.post("/exp/{experiment}/call")
async def call_function(
    body: CallRequest,
    request: Request,
    exp_info: ExperimentInfo = Depends(get_experiment_info),
):
    func = exp_info.functions.get(body.func_name)
    if func and getattr(func, "_leap_adminonly", False):
        if not request.session.get("admin", False):
            raise HTTPException(403, detail="Admin access required")
    try:
        result = await asyncio.to_thread(
            rpc.execute_rpc,
            exp_info,
            func_name=body.func_name,
            args=body.args,
            kwargs=body.kwargs,
            student_id=body.student_id,
            trial=body.trial,
        )
        return {"result": result}
    except rpc.RateLimitError as e:
        raise HTTPException(429, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(403, detail=str(e))
    except RuntimeError as e:
        logger.exception("RPC call failed: %s.%s", exp_info.name, body.func_name)
        raise HTTPException(500, detail=str(e))
