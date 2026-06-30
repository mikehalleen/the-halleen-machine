from form_manager import ProjectFormRegistry


def test_form_registry_loads_registered_paths_with_defaults_and_transforms():
    registry = ProjectFormRegistry()
    name_component = object()
    width_component = object()
    missing_component = object()

    registry.add("project.name", name_component, default="Untitled")
    registry.add("project.width", width_component, default=512, to_ui=lambda value: f"{value}px")
    registry.add("project.missing", missing_component, default="fallback")

    values = registry.load_from_json({"project": {"name": "Demo", "width": 1152}})

    assert values == ["Demo", "1152px", "fallback"]
    assert registry.get_outputs() == [name_component, width_component, missing_component]


def test_form_registry_updates_only_input_paths_and_applies_json_transforms():
    registry = ProjectFormRegistry()
    name_component = object()
    width_component = object()
    display_component = object()

    registry.add("project.name", name_component)
    registry.add("project.width", width_component, to_json=int)
    registry.add("project.display_only", display_component, is_input=False)

    updated = registry.update_json({"project": {"name": "Old", "width": 512}}, "New", "1024")

    assert updated["project"]["name"] == "New"
    assert updated["project"]["width"] == 1024
    assert "display_only" not in updated["project"]
    assert registry.get_inputs() == [name_component, width_component]


def test_form_registry_autovivifies_nested_dictionaries_on_update():
    registry = ProjectFormRegistry()
    registry.add("project.negatives.global", object())

    updated = registry.update_json({"project": {}}, "no watermark")

    assert updated["project"]["negatives"]["global"] == "no watermark"


def test_form_registry_update_json_field_single_path():
    registry = ProjectFormRegistry()
    registry.add("project.width", object(), to_json=int)

    updated = registry.update_json_field({"project": {"name": "A", "width": 512}}, "project.width", "1024")

    assert updated["project"]["name"] == "A"
    assert updated["project"]["width"] == 1024
