# platform_helpers.py
"""
Cross-platform process management for The Halleen Machine.
Abstracts Windows/Linux differences for process spawning and termination.
"""

import platform
import subprocess
import signal
import os
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


class ProcessManager:
    """Cross-platform process management utilities."""
    
    @staticmethod
    def kill_process(pid: int, force: bool = True, wait: bool = True) -> bool:
        """
        Kill a process by PID, cross-platform.
        
        Args:
            pid: Process ID to kill
            force: If True, use SIGKILL (Linux) or /F flag (Windows)
            wait: If True, wait briefly and verify process is dead
            
        Returns:
            True if successful, False otherwise
        """
        import time
        
        if not pid or pid < 0:
            return False
            
        try:
            if IS_WINDOWS:
                # Windows: taskkill with /F (force) and /T (tree - kill children)
                flags = ["/F", "/T"] if force else ["/T"]
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid)] + flags,
                    capture_output=True,
                    check=False
                )
                success = result.returncode == 0
            else:
                # Linux/Unix: use os.kill with appropriate signal
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.kill(pid, sig)
                success = True
                
            # Wait and verify if requested
            if wait and success:
                for _ in range(10):  # Wait up to 1 second
                    time.sleep(0.1)
                    if not ProcessManager.is_process_running(pid):
                        return True
                # Process still running, try one more SIGKILL
                if not IS_WINDOWS:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except:
                        pass
                    
            return success
            
        except ProcessLookupError:
            # Process already dead
            return True
        except PermissionError:
            print(f"[PLATFORM] Permission denied killing PID {pid}")
            return False
        except Exception as e:
            print(f"[PLATFORM] Failed to kill process {pid}: {e}")
            return False
    
    @staticmethod
    def kill_process_tree(pid: int) -> bool:
        """
        Kill a process and all its children, cross-platform.
        
        Args:
            pid: Parent process ID
            
        Returns:
            True if successful, False otherwise
        """
        if not pid or pid < 0:
            return False
            
        try:
            if IS_WINDOWS:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                    check=False
                )
                return result.returncode == 0
            else:
                # Linux: Try to kill process group
                try:
                    # Kill the process group (negative PID)
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    return True
                except (ProcessLookupError, PermissionError):
                    # Fall back to killing just the process
                    try:
                        os.kill(pid, signal.SIGKILL)
                        return True
                    except:
                        return False
        except Exception as e:
            print(f"[PLATFORM] Failed to kill process tree {pid}: {e}")
            return False
    
    @staticmethod
    def launch_detached(
        command: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None
    ) -> subprocess.Popen:
        """
        Launch a detached process that survives parent exit, cross-platform.
        
        Args:
            command: Command and arguments as list
            cwd: Working directory
            env: Environment variables
            
        Returns:
            subprocess.Popen object
        """
        kwargs: Dict[str, Any] = {}
        
        if cwd:
            kwargs["cwd"] = cwd
        if env:
            kwargs["env"] = env
            
        if IS_WINDOWS:
            # Windows: Use DETACHED_PROCESS flag
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS
            kwargs["close_fds"] = True
        else:
            # Linux: Use start_new_session and redirect stdio
            kwargs["start_new_session"] = True
            kwargs["stdout"] = subprocess.DEVNULL
            kwargs["stderr"] = subprocess.DEVNULL
            kwargs["stdin"] = subprocess.DEVNULL
            
        return subprocess.Popen(command, **kwargs)
    
    @staticmethod
    def kill_process_on_port(port: int) -> bool:
        """
        Kill process listening on a specific port, cross-platform.
        
        Args:
            port: Port number
            
        Returns:
            True if successful or no process found, False on error
        """
        try:
            if IS_WINDOWS:
                # PowerShell method
                cmd = [
                    "powershell", "-Command",
                    f"$p = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue; "
                    f"if ($p) {{ Stop-Process -Id $p.OwningProcess -Force -ErrorAction SilentlyContinue }}"
                ]
                subprocess.run(cmd, capture_output=True, check=False)
                return True
            else:
                # Linux: Try multiple methods
                
                # Method 1: lsof
                try:
                    result = subprocess.run(
                        ["lsof", "-ti", f":{port}"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        pids = result.stdout.strip().split('\n')
                        for pid_str in pids:
                            try:
                                pid = int(pid_str.strip())
                                os.kill(pid, signal.SIGKILL)
                            except (ValueError, ProcessLookupError):
                                pass
                        return True
                except FileNotFoundError:
                    pass
                
                # Method 2: fuser
                try:
                    subprocess.run(
                        ["fuser", "-k", f"{port}/tcp"],
                        capture_output=True,
                        check=False
                    )
                    return True
                except FileNotFoundError:
                    pass
                
                # Method 3: ss + /proc (no external tools needed)
                try:
                    result = subprocess.run(
                        ["ss", "-tlnp", f"sport = :{port}"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if result.returncode == 0:
                        # Parse output for PIDs
                        import re
                        for line in result.stdout.split('\n'):
                            # Look for pid=XXXXX pattern
                            match = re.search(r'pid=(\d+)', line)
                            if match:
                                try:
                                    pid = int(match.group(1))
                                    os.kill(pid, signal.SIGKILL)
                                except (ValueError, ProcessLookupError):
                                    pass
                        return True
                except FileNotFoundError:
                    pass
                
                # Method 4: Direct /proc scan (last resort)
                try:
                    import glob
                    for fd_dir in glob.glob('/proc/*/fd'):
                        try:
                            pid = int(fd_dir.split('/')[2])
                            for fd in os.listdir(fd_dir):
                                try:
                                    link = os.readlink(os.path.join(fd_dir, fd))
                                    if f':{port}' in link:
                                        os.kill(pid, signal.SIGKILL)
                                        return True
                                except:
                                    pass
                        except:
                            pass
                except:
                    pass
                
                print(f"[PLATFORM] Could not find tool to kill port {port}")
                return False
                
        except Exception as e:
            print(f"[PLATFORM] Failed to kill process on port {port}: {e}")
            return False
    
    @staticmethod
    def is_process_running(pid: int) -> bool:
        """
        Check if a process is running, cross-platform.
        
        Args:
            pid: Process ID to check
            
        Returns:
            True if running, False otherwise
        """
        if not pid or pid < 0:
            return False
            
        try:
            if IS_WINDOWS:
                # Use tasklist to check
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                return str(pid) in result.stdout
            else:
                # Linux: os.kill with signal 0 checks existence
                os.kill(pid, 0)
                return True
        except (ProcessLookupError, PermissionError):
            return False
        except Exception:
            return False


class PathHelper:
    """Cross-platform path utilities."""
    
    @staticmethod
    def get_python_executable() -> str:
        """
        Get the current Python executable path.
        Works on both Windows and Linux.
        
        Returns:
            Path to Python executable
        """
        return sys.executable
    
    @staticmethod
    def normalize_path(path: str) -> str:
        """
        Normalize a path for the current platform.
        Converts Windows backslashes to forward slashes on Linux.
        
        Args:
            path: Input path string
            
        Returns:
            Normalized path string
        """
        if not path:
            return path
            
        # Convert to Path object and back for normalization
        return str(Path(path))
    
    @staticmethod
    def is_windows_path(path: str) -> bool:
        """
        Check if a path appears to be a Windows-style path.
        
        Args:
            path: Path string to check
            
        Returns:
            True if path looks like Windows path
        """
        if not path:
            return False
        return (
            path.startswith(('C:\\', 'D:\\', 'E:\\', 'c:\\', 'd:\\', 'e:\\'))
            or '\\' in path
        )
    
    @staticmethod
    def convert_windows_path(windows_path: str, linux_base: str = "/workspace") -> str:
        """
        Convert a Windows path to Linux equivalent.
        Maps D:\\ComfyUI -> /workspace/ComfyUI style.
        
        Args:
            windows_path: Windows-style path
            linux_base: Base directory on Linux
            
        Returns:
            Linux-style path
        """
        if not windows_path:
            return windows_path
            
        if not PathHelper.is_windows_path(windows_path):
            return windows_path
            
        # Remove drive letter and convert backslashes
        path = windows_path
        if len(path) >= 2 and path[1] == ':':
            path = path[2:]
        path = path.replace('\\', '/')
        
        # Strip leading slash for joining
        path = path.lstrip('/')
        
        return str(Path(linux_base) / path)


class ComfyUIManager:
    """Cross-platform ComfyUI management utilities."""
    
    @staticmethod
    def extract_port_from_url(url: str, default: int = 8188) -> int:
        """
        Extract port number from a URL string.
        
        Args:
            url: URL like "http://127.0.0.1:4000"
            default: Default port if none found
            
        Returns:
            Port number
        """
        if not url:
            return default
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.port or default
        except:
            return default
    
    @staticmethod
    def restart_comfyui(
        port: int = 8188,
        custom_script: Optional[str] = None,
        python_path: Optional[str] = None,
        main_script: Optional[str] = None,
        listen: str = "0.0.0.0"
    ) -> List[str]:
        """
        Restart ComfyUI server, cross-platform.
        
        Args:
            port: ComfyUI port (default 8188)
            custom_script: Optional custom restart script path
            python_path: Python executable path (if not using custom script)
            main_script: ComfyUI main.py path (if not using custom script)
            listen: Listen address
            
        Returns:
            List of log messages
        """
        log = []
        
        # Step 1: Kill existing process on port
        log.append(f"Killing process on port {port}...")
        ProcessManager.kill_process_on_port(port)
        log.append("Killed old process.")
        
        # Step 2: Launch new process
        if custom_script and os.path.exists(custom_script):
            # Use custom script
            if IS_WINDOWS:
                ProcessManager.launch_detached([custom_script])
            else:
                # Make sure script is executable on Linux
                try:
                    os.chmod(custom_script, 0o755)
                except:
                    pass
                ProcessManager.launch_detached(["bash", custom_script])
            log.append(f"Launched custom script: {custom_script}")
        elif python_path and main_script:
            # Use Python + main.py
            if os.path.exists(python_path) and os.path.exists(main_script):
                cmd = [python_path, main_script, "--listen", listen, "--port", str(port)]
                ProcessManager.launch_detached(
                    cmd,
                    cwd=os.path.dirname(main_script)
                )
                log.append(f"Launched ComfyUI: {' '.join(cmd)}")
            else:
                log.append(f"Could not find Python ({python_path}) or main.py ({main_script})")
        else:
            log.append("No restart method configured. Set 'comfyui_restart_script_path' in settings or configure paths in config.toml.")
            
        return log


# Convenience exports
kill_process = ProcessManager.kill_process
kill_process_tree = ProcessManager.kill_process_tree  
launch_detached = ProcessManager.launch_detached
kill_process_on_port = ProcessManager.kill_process_on_port
is_process_running = ProcessManager.is_process_running
extract_port_from_url = ComfyUIManager.extract_port_from_url