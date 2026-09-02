"""Configurable LoRA causal-language-model training entrypoint."""

import os
from pathlib import Path

from datasets import load_dataset
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments


def main() -> None:
    model_path = os.environ["MODEL_PATH"]
    dataset_path = os.environ["DATASET_PATH"]
    output_path = os.getenv("OUTPUT_PATH", "/output")
    text_column = os.getenv("TEXT_COLUMN", "text")
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype="auto")
    model = get_peft_model(
        model,
        LoraConfig(
            r=int(os.getenv("LORA_R", "16")),
            lora_alpha=int(os.getenv("LORA_ALPHA", "32")),
            target_modules=os.getenv("LORA_TARGETS", "q_proj,v_proj").split(","),
            task_type="CAUSAL_LM",
        ),
    )
    dataset = load_dataset("json", data_files=dataset_path, split="train")

    def tokenize(batch):
        values = tokenizer(
            batch[text_column],
            truncation=True,
            max_length=int(os.getenv("MAX_LENGTH", "2048")),
            padding="max_length",
        )
        values["labels"] = values["input_ids"].copy()
        return values

    tokenized = dataset.map(tokenize, batched=True, remove_columns=dataset.column_names)
    trainer = Trainer(
        model=model,
        train_dataset=tokenized,
        args=TrainingArguments(
            output_dir=output_path,
            num_train_epochs=float(os.getenv("EPOCHS", "1")),
            per_device_train_batch_size=int(os.getenv("BATCH_SIZE", "1")),
            gradient_accumulation_steps=int(os.getenv("GRADIENT_ACCUMULATION", "8")),
            learning_rate=float(os.getenv("LEARNING_RATE", "2e-4")),
            bf16=os.getenv("BF16", "true").lower() == "true",
            save_strategy="epoch",
            logging_steps=10,
            report_to="none",
        ),
    )
    trainer.train()
    trainer.save_model(output_path)
    tokenizer.save_pretrained(output_path)
    Path(output_path, ".training-complete").touch()


if __name__ == "__main__":
    main()
