# Ollama 0.31 Multi-Token Prediction (MTP) Report

**Generated:** Wednesday, 01 July 2026
**Prepared by:** Mounir (AI Assistant)

---

## Executive Summary

Ollama 0.31 introduces **frozen Multi-Token Prediction (MTP)** for Apple Silicon, delivering up to **~90 % faster token generation** for Gemma 4 in coding-agent workloads. This report details the technology, benchmarks, hardware compatibility, and implications for users.

---

## 1. What is Multi-Token Prediction (MTP)?

### 1.1 Core Concept

- **Traditional Inference:** Models predict one token at a time, sequentially.
- **MTP (Frozen Multi-Token Prediction):** Predicts **multiple future tokens in a single inference step**, reducing the number of model calls and improving throughput.
- **Frozen MTP:** A constrained, optimized version of MTP tailored for specific workloads (e.g., coding agents), avoiding overhead when speculation doesn’t help.

### 1.2 Technical Mechanism

- **Parallel Drafting:** MTP drafts multiple tokens in parallel within a single inference pass.
- **Hardware Acceleration:** Leverages **MLX** (Apple’s ML framework) for efficient parallel execution on Apple Silicon (M1/M2/M3).
- **Auto-Tuning:** Ollama dynamically adjusts the number of tokens to draft based on workload, ensuring no slowdown when MTP isn’t beneficial.

---

## 2. Performance Claims & Benchmarks

### 2.1 Claimed Speed-Up

- **~90 % faster token generation** for Gemma 4 on Apple Silicon in coding-agent workloads.
- **Benchmark:** Aider polyglot benchmark (tests multi-language coding agent performance).
- **No change to model output:** Speed-up is purely performance-related; model behavior remains identical.

### 2.2 Benchmark Details

| **Metric**               | **Details**                                                                 |
|--------------------------|-----------------------------------------------------------------------------|
| **Model**                | Gemma 4 (12B parameters, MLX-optimized)                                    |
| **Hardware**             | Apple Silicon (M1/M2/M3, exact chip not specified)                          |
| **Framework**            | MLX                                                                         |
| **Benchmark**            | Aider polyglot (coding agent workload)                                     |
| **Comparison**           | Ollama 0.30 vs. Ollama 0.31                                                |
| **Output Consistency**   | Identical model outputs; only inference speed improved                     |

### 2.3 Limitations

- **No raw tokens/second or latency metrics** provided in official sources.
- **Relative speed-up only:** The 90 % figure is relative to the previous version, not an absolute performance number.
- **Workload-specific:** Speed-up is most pronounced in coding-agent scenarios; general chat may see smaller gains.

---

## 3. Supported Models & Hardware

### 3.1 Models

| **Model**       | **MTP Support** | **Notes**                                  |
|-----------------|-----------------|--------------------------------------------|
| Gemma 4         | ✅ Yes          | First model to receive MTP                 |
| Other Models    | ❌ No (yet)     | May be added in future updates             |

### 3.2 Hardware Compatibility

| **Platform**       | **MTP Support** | **Notes**                                  |
|--------------------|-----------------|--------------------------------------------|
| Apple Silicon      | ✅ Yes          | MLX-optimized; primary target              |
| NVIDIA GPUs        | ❌ No           | Uses GGUF/llama.cpp backend                |
| AMD GPUs           | ❌ No           | Uses GGUF/llama.cpp backend                |
| Intel CPUs         | ❌ No           | Uses GGUF/llama.cpp backend                |

**Key Takeaway:** MTP is **exclusive to Apple Silicon** in Ollama 0.31 due to MLX integration. Other platforms rely on traditional backends (GGUF) and do not benefit from MTP.

---

## 4. How to Use MTP in Ollama 0.31

### 4.1 Prerequisites

- **Apple Silicon Mac** (M1/M2/M3).
- **Ollama 0.31+** installed.
- **Gemma 4 model** (pull the latest version):
  ```bash
  ollama pull gemma4:12b-mlx
  ```

### 4.2 Launching with MTP

- **For coding agents (e.g., Claude):**
  ```bash
  ollama launch claude --model gemma4:12b-mlx
  ```
- **For direct chat:**
  ```bash
  ollama run gemma4:12b-mlx
  ```

### 4.3 Upgrading Existing Models

- Re-pull Gemma 4 to get the MTP-optimized version:
  ```bash
  ollama pull gemma4:12b-mlx
  ```

---

## 5. Implications & Use Cases

### 5.1 Who Benefits?

- **Apple Silicon users** running coding agents (e.g., Aider, Claude).
- **Developers** prototyping or debugging with AI agents.
- **Researchers** experimenting with Gemma 4 on MLX.

### 5.2 Limitations

- **No MTP for non-Apple hardware:** Users on NVIDIA/AMD/Intel must wait for future optimizations or rely on GGUF.
- **Workload-specific gains:** MTP shines in agentic workflows; general chat may see marginal improvements.

---

## 6. Future Outlook

- **Model Expansion:** Other models (e.g., Codex, Droid) may receive MTP support in future Ollama updates.
- **Hardware Expansion:** Potential MTP support for non-Apple platforms if MLX or alternative frameworks (e.g., TensorRT-LLM) are integrated.
- **Benchmark Transparency:** Future releases may include raw performance metrics (tokens/sec, latency) for better comparison.

---

## 7. Sources & References

1. [Ollama Blog: Faster Gemma 4 on MLX with multi-token prediction](https://ollama.com/blog)
2. [AlternativeTo: Gemma 4 is now up to 90% faster on Apple Silicon in Ollama 0.31](https://alternativeto.net/news/2026/7/gemma-4-is-now-up-to-90-faster-on-apple-silicon-in-ollama-0-31/)
3. [ReleaseBot: Ollama Release Notes - June 2026](https://releasebot.io/updates/ollama)
4. [Aider Polyglot Benchmark](https://github.com/paul-gauthier/aider)

---

## 8. Conclusion

Ollama 0.31’s **frozen MTP** is a significant leap for Apple Silicon users, delivering **~90 % faster Gemma 4 inference** in coding-agent workloads. While hardware-limited to Apple Silicon for now, it sets a precedent for future optimizations across platforms. Users on Apple devices should upgrade immediately to leverage the speed-up, while others must wait for broader MTP support.

---

**End of Report**