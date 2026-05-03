"""Open unAirplay DSP panel via pywebview in a subprocess.

pywebview requires the main thread; a subprocess provides its own.
"""

import subprocess
import sys

def _build_dsp_js(device_id: str | None) -> str:
    """Return JS that opens the DSP modal and hides everything else."""
    return f"""
(function() {{
    // Hide body until DSP modal is ready (1s delay)
    document.body.style.visibility = 'hidden';

    var setup = function() {{
        // Set mobile viewport and scale
        var meta = document.createElement('meta');
        meta.name = 'viewport';
        meta.content = 'width=device-width, initial-scale=1.0';
        document.head.appendChild(meta);

        // Open DSP modal for the target device
        var did = '{device_id or ""}';
        if (did && typeof openDspModal === 'function') {{
            openDspModal(did);
        }}

        // Hide everything except the modal + hide scrollbar
        var hide = function() {{
            document.querySelectorAll('.header, .container').forEach(function(el) {{
                el.style.display = 'none';
            }});
            document.body.style.background = '#ffffff';

            var modal = document.getElementById('dsp-modal');
            if (modal) {{
                modal.classList.add('open');
                modal.style.overflow = 'hidden';
                modal.style.paddingTop = '0';
                modal.style.alignItems = 'stretch';
                var inner = modal.querySelector('.modal');
                if (inner) {{
                    inner.style.width = '100%';
                    inner.style.maxWidth = '100%';
                    inner.style.borderRadius = '0';
                    inner.style.margin = '0';
                    inner.style.padding = '12px';
                    inner.style.overflow = 'hidden';
                }}
                // Hide modal header
                var header = modal.querySelector('.modal-header');
                if (header) header.style.display = 'none';
            }}
            document.body.style.overflow = 'hidden';

            // Show the window after everything is ready (1s total)
            document.body.style.visibility = 'visible';
        }};
        setTimeout(hide, 1000);
    }};

    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', function() {{
            setTimeout(setup, 1200);
        }});
    }} else {{
        setTimeout(setup, 800);
    }}
}})();
"""


def _run_webview(dsp_url: str, device_id: str | None) -> None:
    """Entry point for the subprocess — runs webview on its main thread."""
    import webview

    win = webview.create_window("DSP 控制 / unAirplay", dsp_url, width=600,
                                height=820, resizable=False)

    def _on_loaded() -> None:
        win.evaluate_js(_build_dsp_js(device_id))

    win.events.loaded += _on_loaded
    webview.start()


_dsp_process: subprocess.Popen | None = None


def open_dsp(dsp_url: str, device_id: str | None = None) -> None:
    """Launch pywebview DSP window in a subprocess (singleton)."""
    global _dsp_process
    if _dsp_process is not None and _dsp_process.poll() is None:
        return  # already open
    code = (
        f"import sys; sys.path.insert(0, {sys.path[0]!r});"
        f"from vdlna.util.dsp_webview import _run_webview;"
        f"_run_webview({dsp_url!r}, {device_id!r})"
    )
    _dsp_process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
