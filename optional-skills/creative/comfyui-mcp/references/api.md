# ComfyUI REST API Reference

Default: `http://127.0.0.1:8188`. Cloud: `https://cloud.comfy.org/api`.

## Workflow Execution

### POST /prompt — Queue a workflow

```json
{
  "prompt": { "<workflow nodes dict>" },
  "client_id": "optional-uuid"
}
```

Response: `{"prompt_id": "uuid", "number": 1, "node_errors": {}}`

### GET /history/{prompt_id} — Single prompt history

Returns: `{ "prompt_id": { "prompt": [...], "outputs": {...}, "status": {...} } }`

Empty dict `{}` if not yet complete.

### GET /history — All execution history

Query params: `?max_items=200&offset=0`

### POST /interrupt — Stop current generation

### GET /queue — Queue status

Returns: `{"queue_running": [...], "queue_pending": [...]}`

### POST /queue — Manage queue

Body: `{"clear": true}` to clear all, or `{"delete": ["prompt_id1", ...]}`.

## Images

### GET /view — Download image

Query params: `filename` (required), `type` (`output`|`input`|`temp`), `subfolder`.

### POST /upload/image — Upload image

Multipart form: `image` (file), `type` (`input`), `subfolder`, `overwrite` (`true`|`false`).

Response: `{"name": "filename.png", "subfolder": "", "type": "input"}`

## Node/Model Information

### GET /object_info — All node types

Returns every registered node with inputs, outputs, types, defaults, category.

### GET /object_info/{class_type} — Single node info

### GET /models/{folder} — List models

Folders: `checkpoints`, `loras`, `vae`, `controlnet`, `clip`, `clip_vision`,
`upscale_models`, `embeddings`, `unet`, `diffusion_models`.

Returns: array of filename strings.

## System

### GET /system_stats — System information

Returns: OS, Python version, PyTorch version, VRAM per device, RAM total/free.

### POST /free — Free memory

Body: `{"unload_models": true, "free_memory": true}`

## Workflow JSON Format (API Format)

```json
{
  "node_id_string": {
    "class_type": "NodeClassName",
    "inputs": {
      "param_name": "value",
      "linked_input": ["source_node_id", output_index]
    }
  }
}
```

- Node IDs are **strings** (`"3"`, not `3`)
- Links use `["node_id", output_index]` arrays (0-based int)
- `class_type` must match a registered node exactly (case-sensitive)

## WebSocket (real-time progress)

Connect to: `ws://host:8188/ws?clientId={uuid}`

Key events: `execution_start`, `executing` (null = done), `progress`, `execution_success`, `execution_error`.
