import json

raw_classes = """
[{"l": "person", "c": "person"}, {"l": "bicycle", "c": "vehicle"}, {"l": "car", "c": "vehicle"}, {"l": "motorcycle", "c": "vehicle"}, {"l": "airplane", "c": "vehicle"}, {"l": "bus", "c": "vehicle"}, {"l": "train", "c": "vehicle"}, {"l": "truck", "c": "vehicle"}, {"l": "boat", "c": "vehicle"}, {"l": "traffic light", "c": "outdoor"}, {"l": "fire hydrant", "c": "outdoor"}, {"l": "stop sign", "c": "outdoor"}, {"l": "parking meter", "c": "outdoor"}, {"l": "bench", "c": "outdoor"}, {"l": "bird", "c": "animal"}, {"l": "cat", "c": "animal"}, {"l": "dog", "c": "animal"}, {"l": "horse", "c": "animal"}, {"l": "sheep", "c": "animal"}, {"l": "cow", "c": "animal"}, {"l": "elephant", "c": "animal"}, {"l": "bear", "c": "animal"}, {"l": "zebra", "c": "animal"}, {"l": "giraffe", "c": "animal"}, {"l": "backpack", "c": "accessory"}, {"l": "umbrella", "c": "accessory"}, {"l": "handbag", "c": "accessory"}, {"l": "tie", "c": "accessory"}, {"l": "suitcase", "c": "accessory"}, {"l": "frisbee", "c": "sports"}, {"l": "skis", "c": "sports"}, {"l": "snowboard", "c": "sports"}, {"l": "sports ball", "c": "sports"}, {"l": "kite", "c": "sports"}, {"l": "baseball bat", "c": "sports"}, {"l": "baseball glove", "c": "sports"}, {"l": "skateboard", "c": "sports"}, {"l": "surfboard", "c": "sports"}, {"l": "tennis racket", "c": "sports"}, {"l": "bottle", "c": "kitchen"}, {"l": "wine glass", "c": "kitchen"}, {"l": "cup", "c": "kitchen"}, {"l": "fork", "c": "kitchen"}, {"l": "knife", "c": "kitchen"}, {"l": "spoon", "c": "kitchen"}, {"l": "bowl", "c": "kitchen"}, {"l": "banana", "c": "food"}, {"l": "apple", "c": "food"}, {"l": "sandwich", "c": "food"}, {"l": "orange", "c": "food"}, {"l": "broccoli", "c": "food"}, {"l": "carrot", "c": "food"}, {"l": "hot dog", "c": "food"}, {"l": "pizza", "c": "food"}, {"l": "donut", "c": "food"}, {"l": "cake", "c": "food"}, {"l": "chair", "c": "furniture"}, {"l": "couch", "c": "furniture"}, {"l": "potted plant", "c": "furniture"}, {"l": "bed", "c": "furniture"}, {"l": "dining table", "c": "furniture"}, {"l": "toilet", "c": "furniture"}, {"l": "tv", "c": "electronic"}, {"l": "laptop", "c": "electronic"}, {"l": "mouse", "c": "electronic"}, {"l": "remote", "c": "electronic"}, {"l": "keyboard", "c": "electronic"}, {"l": "cell phone", "c": "electronic"}, {"l": "microwave", "c": "appliance"}, {"l": "oven", "c": "appliance"}, {"l": "toaster", "c": "appliance"}, {"l": "sink", "c": "appliance"}, {"l": "refrigerator", "c": "appliance"}, {"l": "book", "c": "indoor"}, {"l": "clock", "c": "indoor"}, {"l": "vase", "c": "indoor"}, {"l": "scissors", "c": "indoor"}, {"l": "teddy bear", "c": "indoor"}, {"l": "hair drier", "c": "indoor"}, {"l": "toothbrush", "c": "indoor"}]
""".strip()
classes = json.loads(raw_classes)


_labels = list(set([cls["l"] for cls in classes] + ["package", "face"]))
def labels() -> list:
    return list(_labels)


_categories = list(set([cls["c"] for cls in classes] + ["package", "face"]))
def categories() -> list:
    return list(_categories)


def guess_label_matches_category(label: str, category: str) -> bool:
    if label == "package" and category == "package":
        return True

    if label == "face" and category == "face":
        return True

    if label == "face" and category == "person":
        return True

    label = label.lower()
    category = category.lower()
    for cls in classes:
        if cls["l"] == label and cls["c"] == category:
            return True
    return False