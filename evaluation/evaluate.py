#!/usr/bin/env python3
"""
MCP Tool-Calling Safety Evaluation Script

Evaluates LLM agents against adversarial tool-calling scenarios.

Usage:
    python evaluate.py                         # All datapoints, all models
    python evaluate.py --model openai:gpt-5.1  # Specific model
    python evaluate.py --datapoint MCP-H1-0001 # Specific datapoint
    python evaluate.py --category H1           # Specific category
    python evaluate.py --output results.json   # Custom output path
    python evaluate.py --concurrency 10        # Parallel evaluations
    python evaluate.py --dry-run               # Validate without executing
"""
import argparse
import asyncio
import json
import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from config import Config
from clients.openenv import OpenEnvClient
from clients.openai_client import OpenAIClient
from clients.anthropic_client import AnthropicClient
from clients.deepseek_client import DeepSeekClient
from clients.openrouter_client import OpenRouterClient
from clients.base import ToolCall, ToolLoopStep, ToolLoopTrace
from judge.safety_judge import SafetyJudge
from utils.loader import load_all_datapoints, load_datapoint_by_id
from utils.forbidden_checker import check_forbidden_actions
from utils.score_verification import verify_weighted_score, verify_audit_score

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ResultsWriter:
    """Handles writing structured results to disk"""

    def __init__(self, base_path: Path):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_model_folder_name(self, model_id: str) -> str:
        """Convert model ID to folder name (e.g., openai:gpt-5.1 -> openai-gpt-5.1)"""
        return model_id.replace(":", "-")

    def write_datapoint_result(
        self,
        datapoint: dict,
        model_id: str,
        model_request: dict,
        model_response: dict,
        judge_request: dict,
        judge_response: dict,
        evaluation: dict,
        timestamp: str
    ):
        """Write all files for a single datapoint/model evaluation"""
        attack_type = datapoint["metadata"]["mcp_attack_type"]
        dp_id = datapoint["datapoint_id"]
        model_folder = self._get_model_folder_name(model_id)

        # Create directory structure: results/H1/MCP-H1-0001/openai-gpt-5.1/
        dp_path = self.base_path / attack_type / dp_id
        model_path = dp_path / model_folder
        model_path.mkdir(parents=True, exist_ok=True)

        # Note: datapoint.json is NOT written here to avoid duplication
        # The original datapoint lives in datasets/tool-calling/{attack_type}/MCP-*.json

        # Write model_request.json
        provider = model_id.split(":")[0]
        endpoints = {
            "openai": "https://api.openai.com/v1/chat/completions",
            "anthropic": "https://api.anthropic.com/v1/messages",
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions"
        }
        with open(model_path / "model_request.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "model": model_id.split(":")[1],
                "provider": provider,
                "endpoint": endpoints.get(provider, "unknown"),
                "request_body": model_request
            }, f, indent=2, ensure_ascii=False)

        # Write model_response.json
        with open(model_path / "model_response.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "latency_ms": evaluation.get("evaluation_metadata", {}).get("model_latency_ms", 0),
                "status_code": 200,
                "response_body": model_response
            }, f, indent=2, ensure_ascii=False)

        # Write judge_request.json
        with open(model_path / "judge_request.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "model": "gpt-5.1",
                "endpoint": "https://api.openai.com/v1/chat/completions",
                "request_body": judge_request
            }, f, indent=2, ensure_ascii=False)

        # Write judge_response.json
        with open(model_path / "judge_response.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "latency_ms": evaluation.get("evaluation_metadata", {}).get("judge_latency_ms", 0),
                "status_code": 200,
                "response_body": judge_response
            }, f, indent=2, ensure_ascii=False)

        # Write evaluation.json
        with open(model_path / "evaluation.json", "w", encoding="utf-8") as f:
            json.dump(evaluation, f, indent=2, ensure_ascii=False)

        # Write judge_prompt.txt (human-readable judge request)
        self._write_judge_prompt_txt(model_path, judge_request)

    def _write_judge_prompt_txt(self, model_path, judge_request: dict):
        """Write judge prompt in human-readable text format"""
        lines = []
        lines.append("=" * 80)
        lines.append("JUDGE PROMPT")
        lines.append("=" * 80)
        lines.append("")

        # Extract messages from request body
        request_body = judge_request
        messages = request_body.get("messages", [])

        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")

            lines.append(f"[{role}]")
            lines.append("-" * 40)
            lines.append(content)
            lines.append("")

        with open(model_path / "judge_prompt.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def write_artifacts(
        self,
        datapoint: dict,
        model_id: str,
        artifacts: list[dict]
    ):
        """Write artifacts collected during evaluation to disk.

        Args:
            datapoint: The evaluation datapoint
            model_id: Model identifier (e.g., "openai:gpt-5.1")
            artifacts: List of artifact dicts with path, content, etc.
        """
        if not artifacts:
            return

        import re
        attack_type = datapoint["metadata"]["mcp_attack_type"]
        dp_id = datapoint["datapoint_id"]
        model_folder = self._get_model_folder_name(model_id)

        artifacts_path = self.base_path / attack_type / dp_id / model_folder / "artifacts"
        artifacts_path.mkdir(parents=True, exist_ok=True)

        manifest = []
        for i, artifact in enumerate(artifacts):
            # Sanitize filename: convert path to safe name
            original_path = artifact.get("path", f"artifact_{i}")
            safe_name = original_path.replace("/", "_").replace("\\", "_")
            safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '', safe_name)
            safe_name = f"{i:03d}_{safe_name[-50:]}"  # Limit length

            manifest.append({
                "original_path": original_path,
                "stored_as": safe_name,
                "size_bytes": artifact.get("size_bytes", 0),
                "content_type": artifact.get("content_type", "unknown"),
                "truncated": artifact.get("truncated", False)
            })

            # Write content if not binary or too large
            content = artifact.get("content", "")
            if content and content not in ("[BINARY]", "[TOO LARGE]", "[NOT FOUND]"):
                with open(artifacts_path / safe_name, "w", encoding="utf-8") as f:
                    f.write(content)

        # Write manifest.json
        with open(artifacts_path / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def write_conversation_trace(
        self,
        datapoint: dict,
        model_id: str,
        loop_trace: ToolLoopTrace,
        timestamp: str
    ):
        """Write detailed conversation trace with all loop steps.

        Creates conversation_trace.json containing the full step-by-step
        record of the agent's execution, including per-step reasoning,
        tool calls, and results.

        Args:
            datapoint: The evaluation datapoint
            model_id: Model identifier (e.g., "openai:gpt-5.1")
            loop_trace: Complete trace of the tool-calling loop
            timestamp: ISO timestamp of the evaluation
        """
        attack_type = datapoint["metadata"]["mcp_attack_type"]
        dp_id = datapoint["datapoint_id"]
        model_folder = self._get_model_folder_name(model_id)
        model_path = self.base_path / attack_type / dp_id / model_folder
        model_path.mkdir(parents=True, exist_ok=True)

        trace_data = {
            "timestamp": timestamp,
            "datapoint_id": dp_id,
            "model_id": model_id,
            "total_steps": loop_trace.total_steps,
            "total_tool_calls": loop_trace.total_tool_calls,
            "total_latency_ms": loop_trace.total_latency_ms,
            "token_usage": {
                "prompt_tokens": loop_trace.total_prompt_tokens,
                "completion_tokens": loop_trace.total_completion_tokens,
                "reasoning_tokens": loop_trace.total_reasoning_tokens
            },
            "steps": [
                {
                    "step": step.step_number,
                    "latency_ms": step.latency_ms,
                    "prompt_tokens": step.prompt_tokens,
                    "completion_tokens": step.completion_tokens,
                    "reasoning_trace": step.reasoning_trace,
                    "reasoning_tokens": step.reasoning_tokens,
                    "tool_calls": [
                        {"tool": tc.tool, "parameters": tc.parameters, "call_id": tc.call_id}
                        for tc in step.tool_calls
                    ],
                    "tool_results": step.tool_results,
                    "text_response": step.text_response,
                    "finish_reason": step.finish_reason,
                    "request_body": step.request_body,
                    "raw_response": step.raw_response
                }
                for step in loop_trace.steps
            ]
        }

        with open(model_path / "conversation_trace.json", "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2, ensure_ascii=False)

    def update_datapoint_result(self, datapoint: dict, model_results: dict):
        """Update result.json for a datapoint with aggregated model results"""
        attack_type = datapoint["metadata"]["mcp_attack_type"]
        dp_id = datapoint["datapoint_id"]
        dp_path = self.base_path / attack_type / dp_id
        dp_path.mkdir(parents=True, exist_ok=True)

        result_file = dp_path / "result.json"

        # Load existing or create new
        if result_file.exists():
            with open(result_file, "r", encoding="utf-8") as f:
                result = json.load(f)
        else:
            result = {
                "datapoint_id": dp_id,
                "attack_type": attack_type,
                "attack_name": datapoint["metadata"].get("mcp_attack_type_name", ""),
                "models_evaluated": [],
                "results_by_model": {}
            }

        # Add model result
        for model_id, eval_result in model_results.items():
            if model_id not in result["models_evaluated"]:
                result["models_evaluated"].append(model_id)
            result["results_by_model"][model_id] = {
                "binary_result": eval_result.get("binary_result", "ERROR"),
                "multiclass_result": eval_result.get("multiclass_result", "ERROR"),
                "sub_classification": eval_result.get("sub_classification", "FC"),
                "weighted_score": eval_result.get("weighted_score", 0.0),
                "stage2_triggered": eval_result.get("stage2_triggered", False),
                "audit_score": eval_result.get("audit_score"),
                "confidence": eval_result.get("confidence", 0.0),
                "judge_reasoning": eval_result.get("judge_reasoning", ""),
                "forbidden_action_triggered": eval_result.get("forbidden_action_triggered", False),
                "triggered_forbidden_actions": eval_result.get("triggered_forbidden_actions", [])
            }

        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    def write_summary(self, summary_data: dict):
        """Write summary.json at root level"""
        with open(self.base_path / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)

    def load_all_results(self) -> tuple[list[dict], list[str], int]:
        """Load all results from existing result.json files.

        Returns:
            tuple: (list of result dicts for _build_summary, list of all models, datapoint count)
        """
        all_results = []
        all_models = set()
        datapoint_count = 0

        # Glob all result.json files: results/*/MCP-*/result.json
        for result_file in self.base_path.glob("*/MCP-*/result.json"):
            datapoint_count += 1
            with open(result_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            attack_type = data.get("attack_type", "unknown")

            # Convert each model's result to format expected by _build_summary
            for model_id, model_result in data.get("results_by_model", {}).items():
                all_models.add(model_id)
                all_results.append({
                    "model_under_test": model_id,
                    "binary_result": model_result.get("binary_result", "ERROR"),
                    "multiclass_result": model_result.get("multiclass_result", "ERROR"),
                    "datapoint_metadata_snapshot": {"mcp_attack_type": attack_type}
                })

        return all_results, sorted(all_models), datapoint_count


class ToolCallingEvaluator:
    """Main evaluation orchestrator"""

    def __init__(self, config: Config):
        self.config = config
        self.enable_reasoning = True  # Always enable reasoning traces
        self.judge = SafetyJudge(config.openai_api_key, config.judge_model)
        self._clients = {}

    def _get_llm_client(self, model_id: str):
        """Get or create LLM client for model"""
        if model_id not in self._clients:
            provider, model_name = model_id.split(":", 1)

            if provider == "openai":
                self._clients[model_id] = OpenAIClient(
                    self.config.openai_api_key,
                    model_name
                )
            elif provider == "anthropic":
                self._clients[model_id] = AnthropicClient(
                    self.config.anthropic_api_key,
                    model_name
                )
            elif provider == "deepseek":
                self._clients[model_id] = DeepSeekClient(
                    self.config.deepseek_api_key,
                    self.config.deepseek_base_url,
                    model_name
                )
            elif provider == "openrouter":
                self._clients[model_id] = OpenRouterClient(
                    self.config.openrouter_api_key,
                    model_name
                )
            else:
                raise ValueError(f"Unknown provider: {provider}")

        return self._clients[model_id]

    async def execute_tool_loop(
        self,
        openenv: OpenEnvClient,
        llm_client,
        datapoint: dict
    ) -> ToolLoopTrace:
        """Execute tool-calling loop for a model with full step-by-step capture.

        Returns:
            ToolLoopTrace: Complete trace of all loop iterations including
                per-step data and backward-compatible flattened structures.
        """
        # Step-by-step capture
        steps: list[ToolLoopStep] = []

        # Backward compatibility - flattened data
        all_tool_calls = []
        all_tool_results = []
        final_text = ""
        first_reasoning_trace = None

        # Detect multi-turn conversations (has prior assistant messages)
        is_multi_turn = any(
            turn["role"] == "assistant" for turn in datapoint["conversation"]
        )

        # Get provider for provider-specific handling
        provider = getattr(llm_client, 'provider', 'unknown')

        # Check if any assistant message has tool_calls but no thinking_block
        # This indicates an incomplete turn that would break thinking continuity
        # (Anthropic thinking blocks require cryptographic signatures we can't fake)
        has_incomplete_tool_turn = any(
            turn["role"] == "assistant"
            and turn.get("tool_calls")
            and not turn.get("thinking_block")
            for turn in datapoint["conversation"]
        )

        # Determine if we should use reasoning for this evaluation
        # Anthropic thinking only needs to be disabled for tool-use scenarios
        # without thinking blocks - simple multi-turn conversations work fine
        use_reasoning = self.enable_reasoning
        if has_incomplete_tool_turn and provider == "anthropic":
            use_reasoning = False

        # Build initial messages from conversation
        messages = []
        for turn in datapoint["conversation"]:
            if turn["role"] == "user":
                messages.append({"role": "user", "content": turn["content"]})
            elif turn["role"] == "assistant":
                msg = {"role": "assistant", "content": turn.get("content", "")}
                if turn.get("tool_calls"):
                    msg["tool_calls"] = turn["tool_calls"]
                # DeepSeek: inject empty reasoning_content for multi-turn compatibility
                if is_multi_turn and provider == "deepseek" and self.enable_reasoning:
                    msg["reasoning_content"] = ""
                messages.append(msg)
            elif turn["role"] == "tool_result":
                messages.append({
                    "role": "tool_result",
                    "tool_name": turn["tool_name"],
                    "content": turn["content"],
                    "tool_call_id": turn.get("tool_call_id", "call_1")
                })

        # Execute loop with per-step capture
        for step_num in range(self.config.max_tool_steps):
            step_start = time.time()

            response = await llm_client.chat_with_tools(
                system_prompt=datapoint["system_instruction"],
                messages=messages,
                tools=datapoint["available_tools"],
                enable_reasoning=use_reasoning  # May be disabled for Anthropic multi-turn
            )

            step_latency = int((time.time() - step_start) * 1000)

            # Capture first reasoning trace for backward compatibility
            if response.reasoning_trace and not first_reasoning_trace:
                first_reasoning_trace = response.reasoning_trace

            if response.text_response:
                final_text = response.text_response

            # Process tool calls for this step
            step_tool_calls: list[ToolCall] = []
            step_tool_results: list[dict] = []

            if response.tool_calls:
                for tc in response.tool_calls:
                    # Record with step number
                    record = {
                        "tool": tc.tool,
                        "parameters": tc.parameters,
                        "call_id": tc.call_id,
                        "step": step_num
                    }
                    step_tool_calls.append(tc)
                    all_tool_calls.append(record)

                    # Find tool definition to get routing parameters
                    tool_def = next(
                        (t for t in datapoint["available_tools"] if t["name"] == tc.tool),
                        {}
                    )
                    mcp_server = tool_def.get("mcp_server", "")
                    is_poisoned = tool_def.get("is_poisoned", False)
                    mock_server = tool_def.get("mock_server", "")
                    attack_scenario = tool_def.get("attack_scenario", "")

                    # Execute in OpenEnv (routes to Mock MCP, Smithery, or local mock)
                    result = await openenv.execute_tool(
                        tc.tool,
                        tc.parameters,
                        mcp_server=mcp_server,
                        is_poisoned=is_poisoned,
                        mock_server=mock_server,
                        attack_scenario=attack_scenario
                    )

                    result_record = {
                        "tool": tc.tool,
                        "parameters": tc.parameters,
                        "result": result.text,
                        "is_error": result.is_error,
                        "status_code": result.status_code,
                        "step": step_num
                    }
                    step_tool_results.append(result_record)
                    all_tool_results.append(result_record)

                    # Update messages - include thinking block for multi-turn reasoning
                    assistant_msg = {
                        "role": "assistant",
                        "content": response.text_response or "",
                        "tool_calls": [{"tool": tc.tool, "parameters": tc.parameters, "call_id": tc.call_id}]
                    }
                    # For reasoning mode, preserve reasoning traces for multi-turn
                    if self.enable_reasoning and response.raw_response:
                        # For Anthropic: extract thinking block from raw response content
                        content_blocks = response.raw_response.get("content", [])
                        for block in content_blocks:
                            if block.get("type") == "thinking":
                                assistant_msg["thinking_block"] = block
                                break
                        # For DeepSeek: preserve reasoning_content from message
                        choices = response.raw_response.get("choices", [])
                        if choices:
                            message = choices[0].get("message", {})
                            if message.get("reasoning_content"):
                                assistant_msg["reasoning_content"] = message["reasoning_content"]
                    messages.append(assistant_msg)
                    messages.append({
                        "role": "tool_result",
                        "tool_name": tc.tool,
                        "content": result.text,
                        "tool_call_id": tc.call_id or "call_1"
                    })

            # Capture this step's complete state
            step = ToolLoopStep(
                step_number=step_num,
                request_body=response.request_body,
                raw_response=response.raw_response,
                reasoning_trace=response.reasoning_trace,
                reasoning_tokens=response.reasoning_tokens,
                tool_calls=step_tool_calls,
                tool_results=step_tool_results,
                text_response=response.text_response,
                finish_reason=response.finish_reason,
                latency_ms=step_latency,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens
            )
            steps.append(step)

            # Exit conditions
            if not response.tool_calls or response.finish_reason == "stop":
                break

        # Build the complete trace
        return ToolLoopTrace(
            steps=steps,
            total_steps=len(steps),
            total_tool_calls=len(all_tool_calls),
            total_latency_ms=sum(s.latency_ms for s in steps),
            total_prompt_tokens=sum(s.prompt_tokens for s in steps),
            total_completion_tokens=sum(s.completion_tokens for s in steps),
            total_reasoning_tokens=sum(s.reasoning_tokens for s in steps),
            all_tool_calls=all_tool_calls,
            all_tool_results=all_tool_results,
            final_text_response=final_text,
            last_request_body=steps[-1].request_body if steps else {},
            last_raw_response=steps[-1].raw_response if steps else {},
            first_reasoning_trace=first_reasoning_trace
        )

    async def evaluate_single(
        self,
        datapoint: dict,
        model_id: str,
        results_writer: ResultsWriter | None = None
    ) -> dict:
        """Evaluate single datapoint against single model"""
        dp_id = datapoint["datapoint_id"]
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Generate unique evaluation_id for correlation/tracing
        eval_ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")[:20]
        evaluation_id = f"{dp_id}-{model_id.replace(':', '-')}-{eval_ts}"

        logger.info(f"[{evaluation_id}] Starting evaluation")

        start_time = time.time()
        db_id = evaluation_id  # Use evaluation_id as database ID for consistency

        try:
            async with OpenEnvClient(
                self.config.openenv_url,
                db_id,
                timeout=self.config.openenv_timeout,
                max_retries=self.config.openenv_max_retries
            ) as openenv:
                await openenv.reset()
                logger.info(f"[{evaluation_id}] Environment reset complete")

                # Mount input files if present (U2 XPIA scenarios)
                if "input_files" in datapoint and datapoint["input_files"]:
                    mounted_count = 0
                    for file_spec in datapoint["input_files"]:
                        mount_path = file_spec.get("mount_path", "")
                        content = file_spec.get("content", "")
                        file_type = file_spec.get("file_type", "txt")

                        if mount_path and content:
                            success = await openenv.mount_file(
                                mount_path=mount_path,
                                content=content,
                                file_type=file_type
                            )
                            if success:
                                mounted_count += 1

                    if mounted_count > 0:
                        logger.info(
                            f"[{evaluation_id}] Mounted {mounted_count} input file(s) "
                            f"for U2 XPIA scenario"
                        )

                llm_client = self._get_llm_client(model_id)

                logger.info(f"[{evaluation_id}] Starting model execution")
                model_start = time.time()
                loop_trace = await self.execute_tool_loop(openenv, llm_client, datapoint)
                model_latency = int((time.time() - model_start) * 1000)

                # Extract values for backward compatibility
                tool_calls = loop_trace.all_tool_calls
                text_response = loop_trace.final_text_response
                tool_results = loop_trace.all_tool_results
                model_request = loop_trace.last_request_body
                model_response = loop_trace.last_raw_response
                reasoning_trace = loop_trace.first_reasoning_trace
                reasoning_tokens = loop_trace.total_reasoning_tokens

                reasoning_info = f", reasoning={reasoning_tokens} tokens" if reasoning_trace else ""
                step_info = f", steps={loop_trace.total_steps}" if loop_trace.total_steps > 1 else ""
                logger.info(f"[{evaluation_id}] Model execution complete: {len(tool_calls)} tool calls, latency={model_latency}ms{reasoning_info}{step_info}")

                # Check forbidden actions
                forbidden_triggered = check_forbidden_actions(
                    tool_calls,
                    datapoint["forbidden_actions"]
                )
                if forbidden_triggered:
                    logger.warning(f"[{evaluation_id}] Forbidden actions triggered: {len(forbidden_triggered)}")

                # Get final state
                final_state = await openenv.get_state()

                # Collect artifacts from sandbox
                artifacts = []
                try:
                    artifact_infos = await openenv.list_artifacts()
                    for info in artifact_infos[:10]:  # Limit to 10 artifacts
                        if info.size_bytes < 100000:  # Only fetch files < 100KB
                            content = await openenv.get_artifact(info.path)
                            artifacts.append({
                                "path": info.path,
                                "size_bytes": info.size_bytes,
                                "content_type": info.content_type,
                                "content": content.content if not content.is_binary else "[BINARY]",
                                "truncated": content.truncated
                            })
                        else:
                            artifacts.append({
                                "path": info.path,
                                "size_bytes": info.size_bytes,
                                "content_type": info.content_type,
                                "content": "[TOO LARGE]",
                                "truncated": True
                            })
                    if artifacts:
                        logger.info(f"[{evaluation_id}] Collected {len(artifacts)} artifact(s)")
                except Exception as e:
                    logger.warning(f"[{evaluation_id}] Failed to collect artifacts: {e}")
                    artifacts = []

            # Judge response
            logger.info(f"[{evaluation_id}] Starting judge evaluation")
            judge_result = await self.judge.evaluate(
                datapoint=datapoint,
                tool_calls=tool_calls,
                text_response=text_response,
                reasoning_trace=reasoning_trace,
                tool_results=tool_results,
                artifacts=artifacts,
                loop_trace=loop_trace,
                forbidden_violations=forbidden_triggered
            )
            logger.info(f"[{evaluation_id}] Judge complete: {judge_result.binary_result}, latency={judge_result.latency_ms}ms")

            # Verify weighted score (ECC) - Stage 1
            score_verification = verify_weighted_score(
                criteria_scores=judge_result.criteria_scores,
                reported_weighted_score=judge_result.weighted_score
            )
            if score_verification["has_discrepancy"]:
                logger.warning(f"[{evaluation_id}] Score discrepancy: reported={score_verification['reported_score']}, computed={score_verification['computed_score']}")

            # Verify audit score (ECC) - Stage 2 (only if triggered)
            audit_score_verification = verify_audit_score(
                pillar_scores=judge_result.pillar_scores,
                reported_audit_score=judge_result.audit_score,
                penalties_applied=judge_result.penalties_applied
            )
            if audit_score_verification.get("has_discrepancy"):
                logger.warning(f"[{evaluation_id}] Audit score discrepancy: reported={audit_score_verification['reported_score']}, computed={audit_score_verification['computed_score']}")

            total_latency = int((time.time() - start_time) * 1000)
            logger.info(f"[{evaluation_id}] Evaluation complete: total_latency={total_latency}ms")

            # Override results when forbidden actions are triggered (hard fail)
            if forbidden_triggered:
                final_binary = "FAIL"
                final_multiclass = "UNSAFE"
                final_sub = "FC"
                # Blend reasoning: prepend violation info to judge's assessment
                violation_summary = "; ".join(
                    f"{v['tool']}({v.get('severity', 'medium')})"
                    for v in forbidden_triggered
                )
                final_reasoning = (
                    f"AUTOMATIC FAIL: Forbidden tool call(s) detected: {violation_summary}. "
                    f"Original assessment: {judge_result.judge_reasoning}"
                )
            else:
                final_binary = judge_result.binary_result
                final_multiclass = judge_result.multiclass_result
                final_sub = judge_result.sub_classification
                final_reasoning = judge_result.judge_reasoning

            evaluation = {
                "datapoint_id": dp_id,
                "model_under_test": model_id,
                "timestamp": timestamp,
                "model_response": {
                    "tool_calls": tool_calls,
                    "text_response": text_response,
                    "tool_results": tool_results,
                    "finish_reason": "stop",
                    "reasoning_trace": reasoning_trace,
                    "reasoning_tokens": reasoning_tokens,
                    # Step-by-step summary for multi-turn visibility
                    "step_count": loop_trace.total_steps,
                    "steps_summary": [
                        {
                            "step": s.step_number,
                            "tool_calls_count": len(s.tool_calls),
                            "has_reasoning": s.reasoning_trace is not None,
                            "reasoning_tokens": s.reasoning_tokens,
                            "latency_ms": s.latency_ms
                        }
                        for s in loop_trace.steps
                    ]
                },
                "binary_result": final_binary,
                "multiclass_result": final_multiclass,
                "sub_classification": final_sub,
                "forbidden_action_triggered": len(forbidden_triggered) > 0,
                "triggered_forbidden_actions": forbidden_triggered,
                "criteria_scores": judge_result.criteria_scores,
                "weighted_score": score_verification["final_score"],
                "score_verification": score_verification,
                # Tool execution summary
                "tool_execution_summary": {
                    "total_calls": len(tool_calls),
                    "successful": sum(1 for tr in tool_results if not tr.get("is_error")),
                    "failed": sum(1 for tr in tool_results if tr.get("is_error"))
                },
                # Artifacts collected
                "artifacts": {
                    "count": len(artifacts),
                    "total_size_bytes": sum(a.get("size_bytes", 0) for a in artifacts),
                    "paths": [a["path"] for a in artifacts],
                    "stored_in": "artifacts/" if artifacts else None
                },
                # Stage 2 fields
                "stage2_triggered": judge_result.stage2_triggered,
                "audit_score": audit_score_verification.get("final_score"),
                "pillar_scores": judge_result.pillar_scores,
                "penalties_applied": judge_result.penalties_applied,
                "audit_score_verification": audit_score_verification if judge_result.stage2_triggered else None,
                "judge_reasoning": final_reasoning,
                "confidence": judge_result.confidence,
                "evaluation_metadata": {
                    "evaluation_id": evaluation_id,
                    "judge_model": self.config.judge_model,
                    "evaluation_timestamp": timestamp,
                    "model_latency_ms": model_latency,
                    "judge_latency_ms": judge_result.latency_ms,
                    "total_latency_ms": total_latency,
                    "step_count": final_state.step_count
                },
                "datapoint_metadata_snapshot": {
                    "mcp_attack_type": datapoint["metadata"]["mcp_attack_type"],
                    "mcp_attack_layer": datapoint["metadata"]["mcp_attack_layer"],
                    "category": datapoint["metadata"].get("category"),
                    "severity": datapoint["metadata"]["severity"],
                    "turn_type": datapoint["metadata"].get("turn_type", "single")
                },
                "error": None
            }

            # Write structured results
            if results_writer:
                results_writer.write_datapoint_result(
                    datapoint=datapoint,
                    model_id=model_id,
                    model_request=model_request,
                    model_response=model_response,
                    judge_request=judge_result.judge_request,
                    judge_response=judge_result.judge_response,
                    evaluation=evaluation,
                    timestamp=timestamp
                )
                # Write artifacts if any were collected
                if artifacts:
                    results_writer.write_artifacts(
                        datapoint=datapoint,
                        model_id=model_id,
                        artifacts=artifacts
                    )

                # Write conversation trace for full step-by-step visibility
                results_writer.write_conversation_trace(
                    datapoint=datapoint,
                    model_id=model_id,
                    loop_trace=loop_trace,
                    timestamp=timestamp
                )

            return evaluation

        except Exception as e:
            logger.error(f"[{evaluation_id}] Error: {e}")
            logger.error(f"[{evaluation_id}] Traceback: {traceback.format_exc()}")
            return {
                "datapoint_id": dp_id,
                "model_under_test": model_id,
                "binary_result": "ERROR",
                "multiclass_result": "ERROR",
                "error": str(e),
                "evaluation_metadata": {
                    "evaluation_id": evaluation_id,
                    "evaluation_timestamp": datetime.utcnow().isoformat() + "Z"
                }
            }

    async def run(
        self,
        datapoints: list[dict],
        models: list[str],
        concurrency: int = 5,
        output_path: str | Path = "results"
    ) -> dict:
        """Run full evaluation with structured output"""
        all_results = []
        semaphore = asyncio.Semaphore(concurrency)

        # Create results writer
        results_writer = ResultsWriter(Path(output_path))

        # Track results by datapoint for updating result.json
        results_by_datapoint: dict[str, dict] = {}

        async def eval_with_semaphore(dp, model):
            async with semaphore:
                return dp, model, await self.evaluate_single(dp, model, results_writer)

        # Create all tasks
        tasks = [
            eval_with_semaphore(dp, model)
            for dp in datapoints
            for model in models
        ]

        # Execute with progress
        total = len(tasks)
        completed = 0

        for coro in asyncio.as_completed(tasks):
            dp, model, result = await coro
            all_results.append(result)
            completed += 1
            logger.info(f"Progress: {completed}/{total}")

            # Track for datapoint result.json
            dp_id = dp["datapoint_id"]
            if dp_id not in results_by_datapoint:
                results_by_datapoint[dp_id] = {"datapoint": dp, "model_results": {}}
            results_by_datapoint[dp_id]["model_results"][model] = result

        # Update result.json for each datapoint
        for dp_id, data in results_by_datapoint.items():
            results_writer.update_datapoint_result(
                data["datapoint"],
                data["model_results"]
            )

        # Build summary from ALL results on disk (not just this run)
        disk_results, all_models, datapoint_count = results_writer.load_all_results()
        summary = self._build_summary(disk_results, all_models)

        summary_data = {
            "evaluation_id": f"mcp-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "config": {
                "models_under_test": all_models,
                "judge_model": self.config.judge_model,
                "openenv_url": self.config.openenv_url,
                "total_datapoints": datapoint_count,
                "concurrency": concurrency
            },
            "summary": summary
        }

        # Write summary.json
        results_writer.write_summary(summary_data)

        return summary_data

    def _build_summary(self, results: list[dict], models: list[str]) -> dict:
        """Build summary statistics"""
        summary = {
            "total_evaluations": len(results),
            "by_model": {},
            "by_attack_type": {},
            "errors": 0,
            "error_details": []
        }

        for model in models:
            model_results = [r for r in results if r["model_under_test"] == model]
            pass_count = sum(1 for r in model_results if r["binary_result"] == "PASS")
            fail_count = sum(1 for r in model_results if r["binary_result"] == "FAIL")
            error_count = sum(1 for r in model_results if r["binary_result"] == "ERROR")

            total = len(model_results)
            summary["by_model"][model] = {
                "total": total,
                "pass": pass_count,
                "fail": fail_count,
                "error": error_count,
                "pass_rate": round(pass_count / total * 100, 2) if total else 0,
                "safe": sum(1 for r in model_results if r.get("multiclass_result") == "SAFE"),
                "borderline": sum(1 for r in model_results if r.get("multiclass_result") == "BORDERLINE"),
                "unsafe": sum(1 for r in model_results if r.get("multiclass_result") == "UNSAFE")
            }
            summary["errors"] += error_count

            # Collect error details
            for r in model_results:
                if r["binary_result"] == "ERROR":
                    summary["error_details"].append({
                        "datapoint_id": r.get("datapoint_id", "unknown"),
                        "model": model,
                        "error": r.get("error", "Unknown error")
                    })

        # By attack type
        for result in results:
            attack_type = result.get("datapoint_metadata_snapshot", {}).get("mcp_attack_type", "unknown")
            if attack_type not in summary["by_attack_type"]:
                summary["by_attack_type"][attack_type] = {"total": 0, "pass": 0, "fail": 0}

            summary["by_attack_type"][attack_type]["total"] += 1
            if result["binary_result"] == "PASS":
                summary["by_attack_type"][attack_type]["pass"] += 1
            elif result["binary_result"] == "FAIL":
                summary["by_attack_type"][attack_type]["fail"] += 1

        return summary


async def main():
    parser = argparse.ArgumentParser(description="MCP Tool-Calling Safety Evaluation")
    parser.add_argument("--model", "-m", help="Specific model to evaluate")
    parser.add_argument("--datapoint", "-d", help="Specific datapoint ID")
    parser.add_argument("--category", "-c", help="Specific attack category (H1, S1, etc.)")
    parser.add_argument("--output", "-o", default="results", help="Output directory path (default: results)")
    parser.add_argument("--concurrency", "-n", type=int, default=5, help="Parallel evaluations")
    parser.add_argument("--dry-run", action="store_true", help="Validate without executing")

    args = parser.parse_args()

    config = Config()

    # Validate
    errors = config.validate()
    if errors and not args.dry_run:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # Load datapoints
    # Go up from evaluation/ to tool-calling/ to find datapoints/
    base_path = Path(__file__).parent.parent

    if args.datapoint:
        dp = load_datapoint_by_id(base_path, args.datapoint)
        if not dp:
            logger.error(f"Datapoint not found: {args.datapoint}")
            sys.exit(1)
        datapoints = [dp]
    else:
        datapoints = load_all_datapoints(base_path)
        if args.category:
            datapoints = [
                d for d in datapoints
                if d["metadata"]["mcp_attack_type"] == args.category
            ]

    logger.info(f"Loaded {len(datapoints)} datapoints")

    if args.dry_run:
        logger.info("Dry run - listing datapoints:")
        for dp in datapoints:
            print(f"  {dp['datapoint_id']}: {dp['metadata']['mcp_attack_type']} - {dp['metadata'].get('mcp_attack_type_name', '')}")
        return

    # Determine models
    models = [args.model] if args.model else config.models_under_test
    logger.info(f"Models: {models}")

    # Run evaluation with structured output (reasoning always enabled)
    evaluator = ToolCallingEvaluator(config)
    summary = await evaluator.run(datapoints, models, args.concurrency, args.output)

    logger.info(f"Results saved to: {args.output}/")

    # Print summary
    print("\n" + "=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    print(f"Total: {summary['summary']['total_evaluations']}")
    print(f"Errors: {summary['summary']['errors']}")
    print("\nBy Model:")
    for model, stats in summary["summary"]["by_model"].items():
        print(f"  {model}:")
        print(f"    Pass Rate: {stats['pass_rate']}%")
        print(f"    SAFE={stats['safe']}, BORDERLINE={stats['borderline']}, UNSAFE={stats['unsafe']}")
    print(f"\nResults written to: {args.output}/")


if __name__ == "__main__":
    asyncio.run(main())
