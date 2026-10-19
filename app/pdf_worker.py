"""Isolated PDF parser. Only bounded JSON leaves this process; no filesystem output."""
import ctypes
import json
import logging
import os
import sys

MEMORY = 512 * 1024 * 1024
EXPANDED = 8 * 1024 * 1024


def memory_limit():
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        return None
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", w.DWORD),
                    ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("active", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.GetCurrentProcess.restype = w.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.process_memory = MEMORY
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise RuntimeError("Memory isolation unavailable")
    return job  # Keep the OS job handle alive until process exit.


def parse(data):
    from io import BytesIO
    from pypdf import PdfReader, Configuration
    from pypdf._configuration import apply_configuration
    from pypdf.generic import StreamObject
    from pypdf.errors import LimitReachedError
    configuration = Configuration(maximum_declared_stream_length=EXPANDED,
        array_based_stream_maximum_output_length=EXPANDED, zlib_maximum_output_length=EXPANDED,
        lzw_maximum_output_length=EXPANDED, run_length_maximum_output_length=EXPANDED,
        brotli_maximum_output_length=EXPANDED, stream_decoding_work_maximum_length=EXPANDED,
        page_tree_maximum_entries=1000, xform_maximum_invocations_per_extraction=1000,
        jbig2dec_binary=None)
    try:
        with apply_configuration(configuration):
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                return {"error": "ENCRYPTED_PDF"}
            count = len(reader.pages)
            if count > 50:
                return {"error": "PDF_TOO_MANY_PAGES"}
            # Decode every referenced stream once before text extraction, including forms/images.
            expanded = 0
            objects = {(number, generation) for generation, entries in reader.xref.items()
                       if generation != 65535 for number in entries if number != 0}
            objects.update((number, 0) for number in reader.xref_objStm)
            for number, generation in objects:
                from pypdf.generic import IndirectObject
                obj = reader.get_object(IndirectObject(number, generation, reader))
                if isinstance(obj, StreamObject):
                    expanded += len(obj.get_data())
                    if expanded > EXPANDED:
                        return {"error": "PDF_EXPANDED_LIMIT"}
            pages, characters = [], 0
            for page in reader.pages:
                text = page.extract_text() or ""
                characters += len(text) + 2
                if characters > 100_000:
                    return {"error": "CONTRACT_TEXT_TOO_LONG"}
                pages.append(text)
            # Worker runs with -I; import this credential-free sibling explicitly.
            import importlib.util
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("pdf_provenance", Path(__file__).with_name("pdf_provenance.py"))
            provenance = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(provenance)
            return {"text": "\n\n".join(pages), "page_count": count,
                    "pdf_provenance": provenance.page_provenance(pages)}
    except LimitReachedError:
        return {"error": "PDF_EXPANDED_LIMIT"}


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        job_handle = memory_limit()
        data = sys.stdin.buffer.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError
        print(json.dumps(parse(data), ensure_ascii=True))
    except BaseException:
        print('{"error":"INVALID_PDF"}')
