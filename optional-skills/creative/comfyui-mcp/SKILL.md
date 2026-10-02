---
name: comfyui-mcp
description: Control a running ComfyUI instance from Hermes — queue workflows, generate images/video, upload inputs, manage models. Use when the user wants to create or modify anything with ComfyUI's node-based generative pipeline.
version: 1.0.0
requires: ComfyUI running locally, remotely, or via Comfy Cloud (default http://127.0.0.1:8188)
author: kshitijk4poor
license: MIT
metadata:
  hermes:
    tags: [comfyui, image-generation, stable-diffusion, flux, creative, generative-ai]
    related_skills: [hermes-blender, stable-diffusion-image-generation, image_gen]
    category: creative
---

# ComfyUI

Control a running ComfyUI instance from Hermes via its REST API. Queue workflow prompts, generate images and video, upload inputs, check progress, and retrieve outputs — all through `execute_code`.

## When to Use

- User asks to generate images with Stable Diffusion, SDXL, Flux, or other diffusion models
- User wants to run a specific ComfyUI workflow
- User wants to chain generative steps (txt2img → upscale → face restore)
- User needs ControlNet, inpainting, img2img, or other advanced pipelines
- User asks to manage ComfyUI queue or check generation progress

## Setup

ComfyUI must be running and reachable. Three options:

### Option A: Local

**Requires Python 3.10+.**

    git clone https://github.com/comfyanonymous/ComfyUI.git
    cd ComfyUI
    python3 -m venv venv && source venv/bin/activate
    pip install torch torchvision torchaudio
    pip install -r requirements.txt
    python main.py --listen 127.0.0.1 --port 8188

GPU acceleration is auto-detected (CUDA on NVIDIA, MPS on Apple Silicon).

### Option B: Comfy Cloud

1. Sign up at https://platform.comfy.org
2. Generate an API key at https://platform.comfy.org/profile/api-keys (**requires paid plan**)
3. Set in `~/.hermes/.env`:

```
COMFYUI_URL=https://cloud.comfy.org/api
COMFYUI_API_KEY=<your-key>
```

### Option C: Remote instance

Point `COMFYUI_URL` at any reachable ComfyUI server:

```
COMFYUI_URL=http://192.168.1.100:8188
```

### Verify connection

```python
from hermes_tools import terminal
r = terminal("curl -s ${COMFYUI_URL:-http://127.0.0.1:8188}/system_stats | python3 -m json.tool | head -5")
print(r["output"])
```

## Core Pattern — ComfyUI Helper

Use this helper inside `execute_code` for all ComfyUI interactions:

```python
import json, time, urllib.request, urllib.parse, urllib.error, uuid, os

COMFY_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
COMFY_API_KEY = os.getenv("COMFYUI_API_KEY", "")

def comfy_api(method, path, data=None, timeout=30):
    """Send a request to the ComfyUI API."""
    url = f"{COMFY_URL}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
    if COMFY_API_KEY:
        req.add_header("X-API-Key", COMFY_API_KEY)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def queue_prompt(workflow, client_id=None):
    """Queue a workflow for execution. Returns prompt_id."""
    client_id = client_id or str(uuid.uuid4())
    result = comfy_api("POST", "/prompt", {
        "prompt": workflow,
        "client_id": client_id,
    })
    return result["prompt_id"]

def wait_for_completion(prompt_id, timeout=300, poll_interval=2):
    """Poll /history until the prompt completes. Returns output dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = comfy_api("GET", f"/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(poll_interval)
    raise TimeoutError(f"Prompt {prompt_id} did not complete in {timeout}s")

def get_image(filename, subfolder="", img_type="output"):
    """Download a generated image. Returns bytes."""
    params = urllib.parse.urlencode({
        "filename": filename, "subfolder": subfolder, "type": img_type
    })
    url = f"{COMFY_URL}/view?{params}"
    req = urllib.request.Request(url)
    if COMFY_API_KEY:
        req.add_header("X-API-Key", COMFY_API_KEY)
    with urllib.request.urlopen(req) as resp:
        return resp.read()

def upload_image(filepath, img_type="input", overwrite=True):
    """Upload an image to ComfyUI. Returns server-side filename."""
    import mimetypes
    boundary = uuid.uuid4().hex
    filename = os.path.basename(filepath)
    mime = mimetypes.guess_type(filepath)[0] or "image/png"

    with open(filepath, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + file_data + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="type"\r\n\r\n'
        f"{img_type}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        f"{'true' if overwrite else 'false'}\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if COMFY_API_KEY:
        req.add_header("X-API-Key", COMFY_API_KEY)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def list_models(folder="checkpoints"):
    """List available models in a folder (checkpoints, loras, vae, etc.)."""
    return comfy_api("GET", f"/models/{folder}")

def get_queue_status():
    """Get current queue (running + pending)."""
    return comfy_api("GET", "/queue")

def interrupt():
    """Interrupt the currently running generation."""
    return comfy_api("POST", "/interrupt")
```

## Common Workflows

### Text-to-Image (Minimal)

Always call `list_models("checkpoints")` first to get the exact filename.

```python
# Discover which checkpoint is installed
models = list_models("checkpoints")
ckpt = models[0]  # use first available

workflow = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 42,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": ckpt},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 512, "height": 512, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "a beautiful sunset over mountains, photorealistic",
            "clip": ["4", 1],
        },
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "ugly, blurry, low quality",
            "clip": ["4", 1],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "hermes", "images": ["8", 0]},
    },
}

pid = queue_prompt(workflow)
result = wait_for_completion(pid)

# Extract output image filename
for node_id, node_output in result["outputs"].items():
    if "images" in node_output:
        for img in node_output["images"]:
            img_data = get_image(img["filename"], img["subfolder"], img["type"])
            with open(f"/tmp/{img['filename']}", "wb") as f:
                f.write(img_data)
            print(f"Saved: /tmp/{img['filename']}")
```

### Parameterized Generation

When the user asks to generate an image, build the workflow by modifying the template:
- **Prompt**: Set node "6" inputs.text to the user's positive prompt
- **Negative**: Set node "7" inputs.text (default: "ugly, blurry, low quality")
- **Model**: Set node "4" inputs.ckpt_name (use `list_models()` to find available ones)
- **Size**: Set node "5" inputs.width/height (SD 1.5: 512, SDXL: 1024, Flux: 1024)
- **Steps/CFG**: Set node "3" inputs.steps and inputs.cfg
- **Seed**: Set node "3" inputs.seed (random for variation, fixed for reproducibility)

### Loading User Workflows

Users often have saved workflow JSON files. Two formats exist:

1. **API format** — flat node dict, directly usable with `queue_prompt()`:
   ```python
   with open("workflow_api.json") as f:
       workflow = json.load(f)
   pid = queue_prompt(workflow)
   ```

2. **UI format** — includes visual layout, NOT directly usable. Look for the
   `"prompt"` key inside the exported data, or ask the user to export as API format
   from ComfyUI's menu: Save (API Format).

### Checking Available Nodes

```python
# List all available node types
info = comfy_api("GET", "/object_info")
print(f"Total node types: {len(info)}")

# Get info for a specific node
ksampler_info = comfy_api("GET", "/object_info/KSampler")
print(json.dumps(ksampler_info, indent=2)[:500])
```

## Queue Management

```python
# Check what's running/pending
status = get_queue_status()
running = status.get("queue_running", [])
pending = status.get("queue_pending", [])
print(f"Running: {len(running)}, Pending: {len(pending)}")

# Cancel everything
if pending:
    comfy_api("POST", "/queue", {"clear": True})

# Interrupt current generation
interrupt()
```

## Advanced: Native MCP Server Integration

For deeper integration with dedicated MCP tools, configure an external ComfyUI
MCP server in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  comfyui:
    command: "npx"
    args: ["-y", "comfyui-mcp-server"]
    env:
      COMFYUI_URL: "http://127.0.0.1:8188"
```

This registers ComfyUI operations as native Hermes tools (prefixed `mcp_comfyui_*`).
See the `native-mcp` skill for MCP server configuration details.

## Pitfalls

1. **Python 3.10+ required**: ComfyUI's dependencies require Python 3.10+.

2. **API format vs UI format**: ComfyUI Save produces UI format (with layout info).
   Only API format works with POST /prompt. Use "Save (API Format)" or extract
   the `"prompt"` key from the UI format JSON.

3. **Node IDs are strings**: Always use `"3"` not `3` in workflow dicts. Links
   between nodes use `["source_node_id", output_index]` arrays.

4. **Model names must be exact**: Use `list_models("checkpoints")` to get the
   exact filename including extension. Names are case-sensitive.

5. **Long generations**: Complex workflows (high steps, large images, video) can
   take minutes. Set `wait_for_completion(timeout=600)` for heavy workloads.

6. **VRAM/memory exhaustion**: Large models + high resolution can OOM. Use
   `comfy_api("POST", "/free", {"unload_models": True})` to free memory between
   generations, or start ComfyUI with `--lowvram` / `--cpu` flags.

7. **Custom nodes**: Many workflows require custom nodes (ControlNet, IPAdapter,
   AnimateDiff, etc.). If a workflow fails with "class_type not found", the user
   needs to install the missing node pack via ComfyUI Manager or manually.

8. **Output path**: Generated images are saved in ComfyUI's `output/` directory.
   Use `get_image()` to download them to a local path the user can access.

9. **Concurrent generations**: ComfyUI queues prompts sequentially by default.
   Multiple `queue_prompt()` calls will queue, not parallelize.

10. **Sampler/scheduler compatibility**: Not all combinations work with all models.
    Safe defaults — SD 1.5/SDXL: `euler` + `normal`, CFG 7.0.
    Flux: `euler` + `simple`, CFG 1.0. SD3: `euler` + `sgm_uniform`, CFG 4.5.
