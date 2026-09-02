"""Launch the pinned official Qwen3-VL trainer with a mounted image dataset."""

import os
import sys

QWEN_ROOT = "/opt/qwen3-vl/qwen-vl-finetune"
sys.path.insert(0, QWEN_ROOT)

from qwenvl.data import data_dict  # noqa: E402
from qwenvl.train.train_qwen import train  # noqa: E402


def setting(name: str, default: str) -> str:
    return os.getenv(name, default)


def main() -> None:
    data_dict["platform"] = {
        "annotation_path": os.environ["ANNOTATION_PATH"],
        "data_path": os.environ["MEDIA_PATH"],
    }
    sys.argv = [
        sys.argv[0],
        "--model_name_or_path",
        setting("MODEL_PATH", "Qwen/Qwen3-VL-4B-Instruct"),
        "--dataset_use",
        "platform",
        "--output_dir",
        setting("OUTPUT_PATH", "/output"),
        "--num_train_epochs",
        setting("EPOCHS", "1"),
        "--per_device_train_batch_size",
        setting("BATCH_SIZE", "1"),
        "--gradient_accumulation_steps",
        setting("GRADIENT_ACCUMULATION", "16"),
        "--learning_rate",
        setting("LEARNING_RATE", "0.00001"),
        "--model_max_length",
        setting("MAX_LENGTH", "4096"),
        "--max_pixels",
        setting("MAX_PIXELS", "50176"),
        "--min_pixels",
        setting("MIN_PIXELS", "784"),
        "--tune_mm_vision",
        "False",
        "--tune_mm_mlp",
        "False",
        "--tune_mm_llm",
        "True",
        "--lora_enable",
        "True",
        "--lora_r",
        setting("LORA_R", "16"),
        "--lora_alpha",
        setting("LORA_ALPHA", "32"),
        "--bf16",
        "True",
        "--gradient_checkpointing",
        "True",
        "--save_strategy",
        "epoch",
        "--logging_steps",
        "10",
        "--report_to",
        "none",
    ]
    train(attn_implementation=setting("ATTENTION_IMPLEMENTATION", "flash_attention_2"))


if __name__ == "__main__":
    main()
