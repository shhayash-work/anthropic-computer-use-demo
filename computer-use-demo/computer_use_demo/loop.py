"""
Agentic sampling loop that calls the Claude API and local implementation of anthropic-defined computer use tools.
"""

import logging
import platform
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

import httpx

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/computer_use_demo.log'),
        logging.StreamHandler()
    ]
)

# Suppress verbose logging from PIL, httpcore, botocore, anthropic, asyncio, and tornado
logging.getLogger('PIL').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.INFO)
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('anthropic._base_client').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)
logging.getLogger('tornado').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Langfuse integration for LLM observability
try:
    from langfuse.decorators import langfuse_context, observe
    LANGFUSE_ENABLED = True
except ImportError:
    LANGFUSE_ENABLED = False
    logger.warning("Langfuse not installed. LLM observability disabled. Install with: pip install langfuse")

from anthropic import (
    Anthropic,
    AnthropicBedrock,
    AnthropicVertex,
    APIError,
    APIResponseValidationError,
    APIStatusError,
)
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaContentBlockParam,
    BetaImageBlockParam,
    BetaMessage,
    BetaMessageParam,
    BetaTextBlock,
    BetaTextBlockParam,
    BetaToolResultBlockParam,
    BetaToolUseBlockParam,
)

from .tools import (
    TOOL_GROUPS_BY_VERSION,
    ToolCollection,
    ToolResult,
    ToolVersion,
)

PROMPT_CACHING_BETA_FLAG = "prompt-caching-2024-07-31"


def conditional_observe(func):
    """Apply @observe decorator only if Langfuse is enabled"""
    if LANGFUSE_ENABLED:
        return observe()(func)
    return func


class APIProvider(StrEnum):
    ANTHROPIC = "anthropic"
    BEDROCK = "bedrock"
    VERTEX = "vertex"


# This system prompt is optimized for the Docker environment in this repository and
# specific tool combinations enabled.
# We encourage modifying this system prompt to ensure the model has context for the
# environment it is running in, and to provide any additional information that may be
# helpful for the task at hand.
SYSTEM_PROMPT = f"""<SYSTEM_CAPABILITY>
* You are utilising an Ubuntu virtual machine using {platform.machine()} architecture with internet access.
* You can feel free to install Ubuntu applications with your bash tool. Use curl instead of wget.
* To open firefox, please just click on the firefox icon.  Note, firefox-esr is what is installed on your system.
* Using bash tool you can start GUI applications, but you need to set export DISPLAY=:1 and use a subshell. For example "(DISPLAY=:1 xterm &)". GUI apps run with bash tool will appear within your desktop environment, but they may take some time to appear. Take a screenshot to confirm it did.
* When using your bash tool with commands that are expected to output very large quantities of text, redirect into a tmp file and use str_replace_based_edit_tool or `grep -n -B <lines before> -A <lines after> <query> <filename>` to confirm output.
* When viewing a page it can be helpful to zoom out so that you can see everything on the page.  Either that, or make sure you scroll down to see everything before deciding something isn't available.
* When using your computer function calls, they take a while to run and send back to you.  Where possible/feasible, try to chain multiple of these calls all into one function calls request.
* The current date is {datetime.today().strftime('%A, %B %-d, %Y')}.
</SYSTEM_CAPABILITY>

<IMPORTANT>
* When using Firefox, if a startup wizard appears, IGNORE IT.  Do not even click "skip this step".  Instead, click on the address bar where it says "Search or enter address", and enter the appropriate search term or URL there.
* If the item you are looking at is a pdf, if after taking a single screenshot of the pdf it seems that you want to read the entire document instead of trying to continue to read the pdf from your screenshots + navigation, determine the URL, use curl to download the pdf, install and use pdftotext to convert it to a text file, and then read that text file directly with your str_replace_based_edit_tool.
* When navigating to a URL mentioned in content, first try to click on the link to navigate. If clicking is not possible or fails, then directly enter the URL into the address bar.
</IMPORTANT>"""


@conditional_observe
async def _call_claude_api(
    client,
    max_tokens: int,
    messages: list,
    model: str,
    system: dict,
    tools: list,
    betas: list,
    extra_body: dict,
    loop_iteration: int,
    provider: APIProvider,
):
    """
    Single Claude API call - logged as separate generation in Langfuse.
    Each call to this function will appear as a distinct generation in the trace.
    """
    raw_response = client.beta.messages.with_raw_response.create(
        max_tokens=max_tokens,
        messages=messages,
        model=model,
        system=[system],
        tools=tools,
        betas=betas,
        extra_body=extra_body,
    )
    
    response = raw_response.parse()
    
    # Log usage to Langfuse if enabled
    if LANGFUSE_ENABLED:
        try:
            langfuse_context.update_current_observation(
                model=model,
                usage={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
                metadata={
                    "provider": str(provider),
                    "loop_iteration": loop_iteration,
                    "stop_reason": response.stop_reason,
                    "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
                }
            )
        except Exception as e:
            logger.warning(f"Failed to log to Langfuse: {e}")
    
    return raw_response, response


@conditional_observe
async def sampling_loop(
    *,
    model: str,
    provider: APIProvider,
    system_prompt_suffix: str,
    messages: list[BetaMessageParam],
    output_callback: Callable[[BetaContentBlockParam], None],
    tool_output_callback: Callable[[ToolResult, str], None],
    api_response_callback: Callable[
        [httpx.Request, httpx.Response | object | None, Exception | None], None
    ],
    api_key: str,
    only_n_most_recent_images: int | None = None,
    max_tokens: int = 4096,
    tool_version: ToolVersion,
    thinking_budget: int | None = None,
    token_efficient_tools_beta: bool = False,
):
    """
    Agentic sampling loop for the assistant/tool interaction of computer use.
    """
    logger.info(f"=== sampling_loop started: model={model}, provider={provider}, tool_version={tool_version} ===")
    
    tool_group = TOOL_GROUPS_BY_VERSION[tool_version]
    tool_collection = ToolCollection(*(ToolCls() for ToolCls in tool_group.tools))
    system = BetaTextBlockParam(
        type="text",
        text=f"{SYSTEM_PROMPT}{' ' + system_prompt_suffix if system_prompt_suffix else ''}",
    )

    loop_iteration = 0
    while True:
        loop_iteration += 1
        logger.info(f"--- Loop iteration {loop_iteration} started ---")
        enable_prompt_caching = False
        betas = [tool_group.beta_flag] if tool_group.beta_flag else []
        if token_efficient_tools_beta:
            betas.append("token-efficient-tools-2025-02-19")
        image_truncation_threshold = only_n_most_recent_images or 0
        if provider == APIProvider.ANTHROPIC:
            client = Anthropic(api_key=api_key, max_retries=4)
            enable_prompt_caching = True
        elif provider == APIProvider.VERTEX:
            client = AnthropicVertex()
        elif provider == APIProvider.BEDROCK:
            client = AnthropicBedrock()

        if enable_prompt_caching:
            betas.append(PROMPT_CACHING_BETA_FLAG)
            _inject_prompt_caching(messages)
            # Because cached reads are 10% of the price, we don't think it's
            # ever sensible to break the cache by truncating images
            only_n_most_recent_images = 0
            # Use type ignore to bypass TypedDict check until SDK types are updated
            system["cache_control"] = {"type": "ephemeral"}  # type: ignore

        # For Bedrock, always limit to max 3 images since caching is not available
        if provider == APIProvider.BEDROCK:
            _maybe_filter_to_n_most_recent_images(
                messages,
                images_to_keep=3,
                min_removal_threshold=0,  # No cache to preserve, so remove immediately
            )
        elif only_n_most_recent_images:
            _maybe_filter_to_n_most_recent_images(
                messages,
                only_n_most_recent_images,
                min_removal_threshold=image_truncation_threshold,
            )
        extra_body = {}
        if thinking_budget:
            # Ensure we only send the required fields for thinking
            extra_body = {
                "thinking": {"type": "enabled", "budget_tokens": thinking_budget}
            }

        # Call the API
        # Each call is logged as a separate generation in Langfuse
        logger.info(f"Calling API: model={model}, max_tokens={max_tokens}, message_count={len(messages)}")
        logger.info(f"messages: {messages}")
        try:
            raw_response, response = await _call_claude_api(
                client=client,
                max_tokens=max_tokens,
                messages=messages,
                model=model,
                system=system,
                tools=tool_collection.to_params(),
                betas=betas,
                extra_body=extra_body,
                loop_iteration=loop_iteration,
                provider=provider,
            )
            logger.info("API call successful")
        except (APIStatusError, APIResponseValidationError) as e:
            logger.error(f"API Status Error: {type(e).__name__}: {str(e)[:200]}")
            api_response_callback(e.request, e.response, e)
            return messages
        except APIError as e:
            logger.error(f"API Error: {type(e).__name__}: {str(e)[:200]}")
            api_response_callback(e.request, e.body, e)
            return messages
        except Exception as e:
            logger.error(f"Unexpected error during API call: {type(e).__name__}: {str(e)[:200]}", exc_info=True)
            raise

        api_response_callback(
            raw_response.http_response.request, raw_response.http_response, None
        )

        logger.debug(f"Response parsed: stop_reason={response.stop_reason}")

        response_params = _response_to_params(response)
        messages.append(
            {
                "role": "assistant",
                "content": response_params,
            }
        )

        tool_result_content: list[BetaToolResultBlockParam] = []
        logger.info(f"Processing response_params, count: {len(response_params)}")
        for content_block in response_params:
            output_callback(content_block)
            if isinstance(content_block, dict) and content_block.get("type") == "tool_use":
                # Type narrowing for tool use blocks
                tool_use_block = cast(BetaToolUseBlockParam, content_block)
                tool_name = tool_use_block["name"]
                tool_input = cast(dict[str, Any], tool_use_block.get("input", {}))
                logger.info(f"Executing tool: {tool_name}, input: {tool_input}")
                try:
                    result = await tool_collection.run(
                        name=tool_name,
                        tool_input=tool_input,
                    )
                    if result.error:
                        logger.warning(f"Tool execution completed with error: {tool_name}, error={result.error[:200]}")
                    else:
                        logger.info(f"Tool execution completed: {tool_name}, success=True")
                except Exception as e:
                    logger.error(f"Tool execution failed: {tool_name}, error: {type(e).__name__}: {str(e)[:200]}", exc_info=True)
                    raise
                
                logger.info(f"Before appending tool result to tool_result_content, current length: {len(tool_result_content)}")
                try:
                    tool_result_content.append(
                        _make_api_tool_result(result, tool_use_block["id"])
                    )
                    logger.info(f"After appending tool result, new length: {len(tool_result_content)}")
                except Exception as e:
                    logger.error(f"Failed to append tool result: {type(e).__name__}: {str(e)}", exc_info=True)
                    raise
                
                logger.info(f"Before calling tool_output_callback for tool_id: {tool_use_block['id']}")
                try:
                    tool_output_callback(result, tool_use_block["id"])
                    logger.info(f"After calling tool_output_callback successfully")
                except Exception as e:
                    logger.error(f"tool_output_callback failed: {type(e).__name__}: {str(e)}", exc_info=True)
                    raise

        logger.info(f"Finished processing all content_blocks, tool_result_content length: {len(tool_result_content)}")
        
        if not tool_result_content:
            logger.info(f"No tool results, ending loop. Total iterations: {loop_iteration}")
            return messages

        logger.debug(f"Adding {len(tool_result_content)} tool results to messages")
        messages.append({"content": tool_result_content, "role": "user"})


def _maybe_filter_to_n_most_recent_images(
    messages: list[BetaMessageParam],
    images_to_keep: int,
    min_removal_threshold: int,
):
    """
    With the assumption that images are screenshots that are of diminishing value as
    the conversation progresses, remove all but the final `images_to_keep` tool_result
    images in place, with a chunk of min_removal_threshold to reduce the amount we
    break the implicit prompt cache.
    
    For Bedrock (min_removal_threshold=0):
    - Always keeps exactly `images_to_keep` (3) most recent images
    - Removes older images immediately without chunking
    
    For Anthropic with caching (min_removal_threshold>0):
    - Removes images in chunks to preserve cache efficiency
    - Example with images_to_keep=3, min_removal_threshold=3:
      * 1-3 images: keep all
      * 4-5 images: keep all (removal would be 1-2, rounded down to 0)
      * 6 images: remove 3, keep 3
      * 7-8 images: remove 3, keep 4-5
      * 9 images: remove 6, keep 3
    """
    if images_to_keep is None:
        return messages

    tool_result_blocks = cast(
        list[BetaToolResultBlockParam],
        [
            item
            for message in messages
            for item in (
                message["content"] if isinstance(message["content"], list) else []
            )
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ],
    )

    total_images = sum(
        1
        for tool_result in tool_result_blocks
        for content in tool_result.get("content", [])
        if isinstance(content, dict) and content.get("type") == "image"
    )

    # Calculate how many images to remove
    images_to_remove = total_images - images_to_keep
    
    # For better cache behavior, we want to remove in chunks
    # When min_removal_threshold=0 (Bedrock), this has no effect and removes immediately
    # When min_removal_threshold>0 (Anthropic), rounds down to nearest multiple
    if min_removal_threshold > 0:
        images_to_remove -= images_to_remove % min_removal_threshold

    # Remove oldest images first (iterate from beginning of messages)
    for tool_result in tool_result_blocks:
        if isinstance(tool_result.get("content"), list):
            new_content = []
            for content in tool_result.get("content", []):
                if isinstance(content, dict) and content.get("type") == "image":
                    if images_to_remove > 0:
                        images_to_remove -= 1
                        continue  # Skip this image (remove it)
                new_content.append(content)
            tool_result["content"] = new_content


def _response_to_params(
    response: BetaMessage,
) -> list[BetaContentBlockParam]:
    res: list[BetaContentBlockParam] = []
    for block in response.content:
        if isinstance(block, BetaTextBlock):
            if block.text:
                res.append(BetaTextBlockParam(type="text", text=block.text))
            elif getattr(block, "type", None) == "thinking":
                # Handle thinking blocks - include signature field
                thinking_block = {
                    "type": "thinking",
                    "thinking": getattr(block, "thinking", None),
                }
                if hasattr(block, "signature"):
                    thinking_block["signature"] = getattr(block, "signature", None)
                res.append(cast(BetaContentBlockParam, thinking_block))
        else:
            # Handle tool use blocks normally
            res.append(cast(BetaToolUseBlockParam, block.model_dump()))
    return res


def _inject_prompt_caching(
    messages: list[BetaMessageParam],
):
    """
    Set cache breakpoints for the 3 most recent turns
    one cache breakpoint is left for tools/system prompt, to be shared across sessions
    """

    breakpoints_remaining = 3
    for message in reversed(messages):
        if message["role"] == "user" and isinstance(
            content := message["content"], list
        ):
            if breakpoints_remaining:
                breakpoints_remaining -= 1
                # Use type ignore to bypass TypedDict check until SDK types are updated
                content[-1]["cache_control"] = BetaCacheControlEphemeralParam(  # type: ignore
                    {"type": "ephemeral"}
                )
            else:
                if isinstance(content[-1], dict) and "cache_control" in content[-1]:
                    del content[-1]["cache_control"]  # type: ignore
                # we'll only every have one extra turn per loop
                break


def _make_api_tool_result(
    result: ToolResult, tool_use_id: str
) -> BetaToolResultBlockParam:
    """Convert an agent ToolResult to an API ToolResultBlockParam."""
    tool_result_content: list[BetaTextBlockParam | BetaImageBlockParam] | str = []
    is_error = False
    if result.error:
        is_error = True
        tool_result_content = _maybe_prepend_system_tool_result(result, result.error)
    else:
        if result.output:
            tool_result_content.append(
                {
                    "type": "text",
                    "text": _maybe_prepend_system_tool_result(result, result.output),
                }
            )
        if result.base64_image:
            tool_result_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": result.base64_image,
                    },
                }
            )
    return {
        "type": "tool_result",
        "content": tool_result_content,
        "tool_use_id": tool_use_id,
        "is_error": is_error,
    }


def _maybe_prepend_system_tool_result(result: ToolResult, result_text: str):
    if result.system:
        result_text = f"<system>{result.system}</system>\n{result_text}"
    return result_text
