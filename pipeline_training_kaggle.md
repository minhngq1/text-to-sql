# Pipeline huấn luyện — Qwen2.5-Coder-7B-Instruct QLoRA (Unsloth, Kaggle)

Nguồn: notebook Kaggle, đã bỏ log cài đặt thư viện và các dòng in màn hình không cần thiết.
Logic không đổi so với bản gốc.

## 1. Nạp model gốc

```python
from unsloth import FastLanguageModel
import torch

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/Qwen2.5-Coder-7B-Instruct",
    max_seq_length = 2048,
    load_in_4bit = True,
)
```

## 2. Cấu hình LoRA

```python
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16,
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth", random_state=3407,
)
```

> `lora_dropout` không được truyền vào lệnh này — dùng mặc định của Unsloth (0).

## 3. Nạp và xử lý tập dữ liệu

```python
import json, glob, os

paths = glob.glob("/kaggle/input/**/dataset.json", recursive=True)
if not paths:
    raise FileNotFoundError(
        "Không thấy dataset.json! Hãy bấm '+ Add Input' bên phải để upload file."
    )
data_path = paths[0]

with open(data_path, encoding="utf-8") as f:
    raw = json.load(f)

SYSTEM_SQL = ("Bạn là trợ lý chuyển câu hỏi tiếng Việt thành câu lệnh SQL PostgreSQL "
              "chính xác dựa trên schema được cung cấp. Chỉ trả về câu SQL.")
SYSTEM_EXPLAIN = ("Bạn là trợ lý giải thích câu lệnh SQL PostgreSQL bằng tiếng Việt "
                   "dễ hiểu, dựa trên schema cơ sở dữ liệu được cung cấp.")

def to_messages(ex):
    system = SYSTEM_EXPLAIN if ex.get("category") == "explain" else SYSTEM_SQL
    user = ex["instruction"].strip() + "\n\n" + ex["input"].strip()
    return {"messages": [
        {"role": "system",    "content": system},
        {"role": "user",      "content": user},
        {"role": "assistant", "content": ex["output"].strip()},
    ]}

from datasets import Dataset
dataset = Dataset.from_list([to_messages(e) for e in raw])

def format_chat(ex):
    return {"text": tokenizer.apply_chat_template(
        ex["messages"], tokenize=False, add_generation_prompt=False)}

dataset = dataset.map(format_chat)
```

> Số mẫu = `len(raw)`, chưa có con số cụ thể — cần chạy và lấy giá trị thật.
> Dữ liệu có 2 loại: câu hỏi → SQL (`SYSTEM_SQL`) và giải thích SQL (`SYSTEM_EXPLAIN`),
> phân biệt bằng trường `category` trong `dataset.json`.
> **Không có bước `train_test_split`** — toàn bộ dữ liệu dùng để train, không chia validation.

## 4. Cấu hình huấn luyện

```python
from trl import SFTTrainer, SFTConfig

max_seq_length = 2048

trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    args = SFTConfig(
        dataset_text_field = "text",
        max_seq_length = max_seq_length,
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 4,
        warmup_steps = 5,
        num_train_epochs = 3,
        learning_rate = 2e-4,
        logging_steps = 5,
        optim = "adamw_8bit",
        weight_decay = 0.01,
        lr_scheduler_type = "linear",
        seed = 3407,
        output_dir = "outputs",
        report_to = "none",
    ),
)
```

> Batch size hiệu dụng = `per_device_train_batch_size × gradient_accumulation_steps` = 2 × 4 = **8**.

## 5. Chạy huấn luyện

```python
trainer_stats = trainer.train()
```

## 6. Lưu model đã gộp LoRA (dạng HuggingFace)

```python
model.save_pretrained("modelai_text2sql")
tokenizer.save_pretrained("modelai_text2sql")
```

## 7. Xuất GGUF

```python
model.save_pretrained_gguf("modelai_text2sql_gguf", tokenizer,
                           quantization_method="q4_k_m")
```

> Một lệnh duy nhất của Unsloth gộp cả merge adapter + convert GGUF + quantize Q4_K_M.

## Demo kiểm tra (không thuộc pipeline huấn luyện)

```python
sql_can_giai_thich = "SELECT c.company_name, COUNT(*) FROM supply_contracts sc JOIN companies c ON sc.company_id = c.id GROUP BY c.company_name ORDER BY COUNT(*) DESC LIMIT 5;"

messages2 = [
    {"role": "system", "content": SYSTEM_EXPLAIN},
    {"role": "user",   "content": "Giải thích câu SQL sau bằng tiếng Việt dễ hiểu:\n\n" + SCHEMA + "\n\nSQL cần giải thích:\n" + sql_can_giai_thich},
]
inputs2 = tokenizer.apply_chat_template(
    messages2, tokenize=True, add_generation_prompt=True, return_tensors="pt"
).to("cuda")

out2 = model.generate(input_ids=inputs2, max_new_tokens=256,
                      temperature=0.1, do_sample=False)
answer2 = tokenizer.decode(out2[0][inputs2.shape[1]:], skip_special_tokens=True)
```

> Biến `SCHEMA` không được định nghĩa trong đoạn code đã gửi — cell gốc thiếu hoặc chưa
> được cung cấp đầy đủ.

---

## Bảng tóm tắt siêu tham số

| Hạng mục | Giá trị |
|---|---|
| Model gốc | `unsloth/Qwen2.5-Coder-7B-Instruct` |
| Thư viện fine-tune | Unsloth (`FastLanguageModel`) + TRL (`SFTTrainer`) |
| Lượng tử hoá lúc train | 4-bit (`load_in_4bit = True`) |
| `max_seq_length` | 2048 |
| LoRA `r` | 16 |
| LoRA `alpha` | 16 |
| LoRA `dropout` | không đặt — mặc định Unsloth (0) |
| `target_modules` | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Batch size / hiệu dụng | 2 × 4 (gradient accumulation) = 8 |
| Epoch | 3 |
| Learning rate | 2e-4, `linear` scheduler, warmup 5 step |
| Optimizer | adamw_8bit, weight_decay 0.01 |
| Seed | 3407 |
| Số cặp huấn luyện | **chưa xác định — cần chạy lấy `len(raw)`** |
| Chia train/validation | **không có** |
| Công cụ xuất GGUF | `model.save_pretrained_gguf(...)` của Unsloth, `q4_k_m` |
