# ComfyUI Workflow Recipes

Ready-to-use workflow templates. Always call `list_models("checkpoints")` first
to discover the exact checkpoint filename on the user's system.

## SDXL Text-to-Image

```python
workflow = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "SDXL_CHECKPOINT_HERE"},
    },
    "2": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "POSITIVE PROMPT", "clip": ["1", 1]},
    },
    "3": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "ugly, blurry, low quality, deformed", "clip": ["1", 1]},
    },
    "4": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": 25, "cfg": 7.0,
            "sampler_name": "euler_ancestral", "scheduler": "normal",
            "denoise": 1.0,
            "model": ["1", 0], "positive": ["2", 0],
            "negative": ["3", 0], "latent_image": ["4", 0],
        },
    },
    "6": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
    },
    "7": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "hermes_sdxl", "images": ["6", 0]},
    },
}
```

SDXL sizes: 1024×1024, 1152×896, 896×1152. Steps 20-30. CFG 5-9.

## Image-to-Image

```python
# Upload the input image first
result = upload_image("/path/to/input.png")
input_name = result["name"]

workflow = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "CHECKPOINT"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": input_name}},
    "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "POSITIVE", "clip": ["1", 1]}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly, blurry", "clip": ["1", 1]}},
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": 20, "cfg": 7.0,
            "sampler_name": "euler", "scheduler": "normal",
            "denoise": 0.6,
            "model": ["1", 0], "positive": ["4", 0],
            "negative": ["5", 0], "latent_image": ["3", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "hermes_img2img", "images": ["7", 0]}},
}
```

Key: **denoise** (0.3 = subtle, 0.6 = moderate, 0.9 = heavy changes).

## Flux Text-to-Image

Flux uses separate UNET/CLIP/VAE loaders (not CheckpointLoaderSimple).

```python
workflow = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "FLUX_UNET", "weight_dtype": "default"}},
    "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "T5_CLIP", "clip_name2": "CLIP_L", "type": "flux"}},
    "3": {"class_type": "VAELoader", "inputs": {"vae_name": "VAE_NAME"}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "PROMPT", "clip": ["2", 0]}},
    "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "6": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0, "steps": 20, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0,
            "model": ["1", 0], "positive": ["4", 0],
            "negative": ["4", 0], "latent_image": ["5", 0],
        },
    },
    "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "hermes_flux", "images": ["7", 0]}},
}
```

Flux: CFG 1.0, `euler` + `simple`. Negative prompt has minimal effect.

## Execution Pattern (all recipes)

```python
pid = queue_prompt(workflow)
result = wait_for_completion(pid, timeout=300)

for node_id, node_output in result["outputs"].items():
    if "images" in node_output:
        for img_info in node_output["images"]:
            data = get_image(img_info["filename"], img_info["subfolder"], img_info["type"])
            local_path = f"/tmp/{img_info['filename']}"
            with open(local_path, "wb") as f:
                f.write(data)
            print(f"Saved: {local_path} ({len(data)} bytes)")
```
