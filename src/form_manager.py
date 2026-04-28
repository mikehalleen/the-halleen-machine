# form_manager.py
import json
import gradio as gr
from typing import Any, Callable, Dict, List, Tuple


class ProjectFormRegistry:
    """
    Manages a registry of UI components mapped to paths in the project JSON.
    Allows for:
      1. Centralized definition of UI elements (build time).
      2. Automated loading of values from a JSON string (runtime).
      3. Automated updating of a JSON string from UI values (runtime).
    
    PHASE 2B: Modified to work with dict-only preview_code.
    """

    def __init__(self):
        # List of registry entries to allow multiple components per path
        self._registry: List[Dict[str, Any]] = []

    def add(self, 
            json_path: str, 
            component: gr.components.Component, 
            default: Any = None, 
            to_ui: Callable[[Any], Any] = None, 
            to_json: Callable[[Any], Any] = None,
            is_input: bool = True):
        
        entry = {
            "path": json_path,
            "component": component,
            "default": default,
            "to_ui": to_ui,
            "to_json": to_json,
            "is_input": is_input
        }
        self._registry.append(entry)
        return component

    def get_inputs(self):
        """Returns the list of components registered as inputs (for change events)."""
        return [entry["component"] for entry in self._registry if entry["is_input"]]

    def get_outputs(self):
        """Returns the full list of registered components (for load updates)."""
        return [entry["component"] for entry in self._registry]
    

    def load_from_json(self, json_data: str | dict):
        data = json_data
        outputs = []
        
        for entry in self._registry:
            path = entry["path"]
            raw_val = self._get_value_by_path(data, path, entry["default"])
            
            if entry["to_ui"]:
                try:
                    val = entry["to_ui"](raw_val)
                except Exception as e:
                    print(f"Error transforming {path} to UI: {e}")
                    val = raw_val
            else:
                val = raw_val
            
            outputs.append(val)
            
        return outputs

    def update_json(self, current_json: str | dict, *component_values):
        import copy
        data = copy.deepcopy(current_json)
        
        # Get only the entries that correspond to inputs (matching get_inputs order)
        input_entries = [entry for entry in self._registry if entry["is_input"]]
        
        if len(component_values) != len(input_entries):
            print(f"Warning: Form registry mismatch. Got {len(component_values)} values for {len(input_entries)} inputs.")
            return data

        for entry, val in zip(input_entries, component_values):
            path = entry["path"]
            
            if entry["to_json"]:
                try:
                    final_val = entry["to_json"](val)
                except Exception as e:
                    final_val = val
            else:
                final_val = val
                
            self._set_value_by_path(data, path, final_val)
        
        return data
    


    def _get_value_by_path(self, data: dict, path: str, default: Any):
        keys = path.split('.')
        curr = data
        try:
            for k in keys:
                if isinstance(curr, dict):
                    curr = curr.get(k, None)
                elif isinstance(curr, list):
                    # Attempt to handle list index if path uses numbers
                    idx = int(k)
                    if 0 <= idx < len(curr):
                        curr = curr[idx]
                    else:
                        return default
                else:
                    return default
                
                if curr is None:
                    return default
            return curr
        except Exception:
            return default

    def _set_value_by_path(self, data: dict, path: str, value: Any):
        keys = path.split('.')
        curr = data
        for i, k in enumerate(keys[:-1]):
            # Auto-vivify dictionaries
            if k not in curr or not isinstance(curr[k], (dict, list)):
                curr[k] = {}
            
            curr = curr[k]
            
        last_key = keys[-1]
        if isinstance(curr, dict):
            curr[last_key] = value
        elif isinstance(curr, list):
            try:
                idx = int(last_key)
                if 0 <= idx < len(curr):
                    curr[idx] = value
            except:
                pass
