"""Prepare the first request of an executable Turn from its exact visible members."""

import json

from .contracts import DisclosureLabel, LoopRejected
from .domain import join_labels
from .execution_contracts import (
    ExecutionModelAttempt,
    ExecutionRunRecord,
    ExecutionVisibilityManifest,
)
from .execution_parsing import EXECUTION_TOOLS, execution_response_schema


def execution_context_label(run: ExecutionRunRecord) -> DisclosureLabel:
    return join_labels(
        (
            DisclosureLabel(
                value="ENDPOINT_RESTRICTED",
                allowed_endpoints=(run.origin.recipient.endpoint.identity,),
            ),
            *(
                attempt.manifest.joined_label
                for turn in run.turns[:-1]
                for attempt in turn.attempts
            ),
        )
    )


def prepare_execution_request(
    run: ExecutionRunRecord, manifest: ExecutionVisibilityManifest
) -> ExecutionRunRecord:
    run = ExecutionRunRecord.model_validate_json(run.canonical_bytes())
    manifest = ExecutionVisibilityManifest.model_validate_json(manifest.canonical_bytes())
    if (
        run.state != "ACTIVE"
        or not run.turns
        or run.head != "loop:" + run.model_copy(update={"head": "pending"}).digest()
    ):
        raise LoopRejected("missing active self-bound execution Run")
    if run.policy.live_model is not None:
        raise LoopRejected("executable request requires registered hermetic transport")
    turn = run.turns[-1]
    if (
        turn.state != "PREPARING"
        or turn.attempts
        or not turn.accumulator
        or turn.selector != 0
        or turn.response_seal is not None
        or turn.initialized_calls is not None
    ):
        raise LoopRejected("duplicate attempt lineage or missing unsealed visibility")
    if (
        manifest.members != turn.accumulator
        or manifest.tenant != run.tenant
        or manifest.principal != run.principal
        or manifest.contour_head != run.contour_head
        or manifest.run_id != run.run_id
        or manifest.turn_id != turn.turn_id
        or manifest.generation != 0
        or manifest.worker_session != run.worker_session
        or len({member.record_id for member in manifest.members}) != len(manifest.members)
    ):
        raise LoopRejected(
            "manifest differs from original identity or exact accumulated visibility"
        )
    if manifest.joined_label != join_labels(tuple(member.label for member in manifest.members)):
        raise LoopRejected("manifest disclosure label differs from complete join")
    artifact = manifest.artifact
    if (
        artifact.tools != EXECUTION_TOOLS
        or artifact.response_schema_json != execution_response_schema()
    ):
        raise LoopRejected("unregistered executable generator schema or tools")
    prompt = [member for member in manifest.members if member.surface == "prompt"]
    schema = [member for member in manifest.members if member.surface == "schema"]
    context = [member for member in manifest.members if member.surface == "context"]
    if (
        len(prompt) != 1
        or len(schema) != 1
        or len(context) != 1
        or prompt[0].content != artifact.rendered
        or prompt[0].revision_head != artifact.content_hash
        or schema[0].content != artifact.response_schema_json
        or schema[0].revision_head != artifact.digest()
        or context[0].provenance_head != run.predecessor
        or context[0].content not in artifact.rendered
        or context[0].label != execution_context_label(run)
        or prompt[0].label != context[0].label
    ):
        raise LoopRejected("request lacks exact prompt/schema/context visibility closure")
    request = json.dumps(
        {
            "prompt": artifact.rendered,
            "schema": artifact.response_schema_json,
            "manifest": manifest.digest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(request.encode()) > run.policy.max_request_bytes:
        raise LoopRejected("request exceeds physical transport bound")
    lineage = turn.turn_id + "/slot/0"
    attempt = ExecutionModelAttempt(
        attempt_id=lineage + "/generation/0",
        lineage_id=lineage,
        generation=0,
        state="PREPARED_NOT_EMITTED",
        head="pending",
        manifest=manifest,
        request=request,
        worker_session=manifest.worker_session,
        provider_contract="hermetic-model.v1",
        recipient="hermetic-model",
        live_model=None,
        response_base64=None,
        receipt=None,
        rejection=None,
    )
    attempt = attempt.model_copy(update={"head": "execution-attempt:" + attempt.digest()})
    turn = turn.model_copy(
        update={"head": "pending", "attempts": (attempt,), "state": "CALL_ACTIVE"}
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    result = run.model_copy(
        update={
            "head": "pending",
            "predecessor": run.head,
            "event": "ModelAttemptPrepared",
            "turns": (*run.turns[:-1], turn),
        }
    )
    return result.model_copy(update={"head": "loop:" + result.digest()})
