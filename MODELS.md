# Model Credits & Licenses

This tool is designed to work with the following AI models. All models remain under their original licenses. This file provides attribution and links to original sources.

**Note:** These models are not included in this repository. Users must download them separately or use the tool's download helpers.

---

## Image Generation Models

### SDXL 1.0 (with VAE Fix)
- **File:** `sdXL_v10VAEFix.safetensors`
- **Author:** Stability AI
- **License:** [CreativeML Open RAIL++-M](https://github.com/Stability-AI/generative-models/blob/main/model_licenses/LICENSE-SDXL1.0)
- **Source:** [Hugging Face](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)
- **Usage:** Default checkpoint for image generation

### Obsession Illustrious v2.1
- **File:** `obsessionIllustrious_v21.safetensors`
- **Author:** rqdwdw
- **License:** [Fair AI Public License 1.0-SD](https://freedevproject.org/faipl-1.0-sd/)
- **Source:** [Civitai](https://civitai.com/models/820208/obsession-illustrious-xl)
- **Usage:** Enhanced pose generation

### Juggernaut XL Inpainting
- **File:** `juggernautxl-inpainting.safetensors`
- **Author:** KandooAI / WereCatf
- **License:** CreativeML Open RAIL++-M
- **Source:** [Civitai](https://civitai.com/models/403361/juggernaut-xl-inpainting)
- **Usage:** Inpainting and healing operations

---

## ControlNet

### ControlNet Union ProMax (SDXL)
- **File:** `diffusion_pytorch_model_promax.safetensors`
- **Author:** xinsir
- **License:** Apache 2.0
- **Source:** [Hugging Face](https://huggingface.co/xinsir/controlnet-union-sdxl-1.0)
- **Usage:** Pose and depth control for image generation

---

## Video Generation Models (Wan 2.2)

All Wan 2.2 models are developed by Alibaba and released under the **Apache 2.0** license.

- **Source:** [Hugging Face](https://huggingface.co/Wan-AI) | [GitHub](https://github.com/Wan-Video/Wan2.1)

### Text Encoder
- **File:** `umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- **Usage:** Text encoding for video generation

### VAE
- **File:** `wan_2.1_vae.safetensors`
- **Usage:** Video encoding/decoding

### Diffusion Models
- **File:** `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`
- **File:** `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors`
- **Usage:** Image-to-video generation (14B parameter models)

### LoRAs
- **File:** `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors`
- **File:** `wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors`
- **Usage:** Acceleration LoRAs for faster video generation

---

## Upscaling

### 4x NMKD Siax
- **File:** `4x_NMKD-Siax_200k.pth`
- **Author:** NMKD (N00MKRAD)
- **License:** [WTFPL](http://www.wtfpl.net/)
- **Source:** [OpenModelDB](https://openmodeldb.info/models/4x-NMKD-Siax-CX)
- **Usage:** 4x image upscaling

---

## Frame Interpolation

### RIFE (Real-Time Intermediate Flow Estimation)
- **File:** `xrife47.pth`
- **Authors:** Zhewei Huang, Tianyuan Zhang, Wen Heng, Boxin Shi, Shuchang Zhou
- **License:** MIT
- **Source:** [GitHub](https://github.com/hzwer/Practical-RIFE)
- **Paper:** [ECCV 2022](https://arxiv.org/abs/2011.06294)
- **Usage:** Video frame interpolation

### FILM (Frame Interpolation for Large Motion)
- **File:** `film_net_fp32.pt`
- **Author:** Google Research
- **License:** Apache 2.0
- **Source:** [GitHub](https://github.com/google-research/frame-interpolation)
- **Paper:** [ECCV 2022](https://arxiv.org/abs/2202.04901)
- **Usage:** Video frame interpolation

---

## License Summary

| License | Models |
|---------|--------|
| Apache 2.0 | Wan 2.2 (all), ControlNet Union, FILM |
| CreativeML Open RAIL++-M | SDXL, Juggernaut XL Inpainting |
| Fair AI Public License 1.0-SD | Obsession Illustrious |
| MIT | RIFE |
| WTFPL | 4x NMKD Siax |

---

## Acknowledgments

This tool would not be possible without the incredible work of the open-source AI community. Thank you to all the researchers, developers, and creators who make their work freely available.

Special thanks to the [ComfyUI](https://github.com/comfyanonymous/ComfyUI) project for providing the foundation that this tool builds upon.
