from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from job_comparison_app.pdf_copy import (
    CopyStatus as S, PdfCopyRequest, prepare_pdf_copy, resolve_decisions, commit_pdf_copy,
)
from job_comparison_app.errors import InputChangedError, InputValidationError
from job_comparison_app.application import JobComparisonApplication
from job_comparison_app.gui.controller import Controller
from job_comparison_app.gui.view import TkView
from job_comparison_app.gui.pdf_outputs import OutputConflictChoice


def prepare(folder, text):
    return prepare_pdf_copy(PdfCopyRequest(folder, text))


def test_normalize_validate_and_exact_lookup(tmp_path):
    (tmp_path / 'A&B.PDF').write_bytes(b'pdf')
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested' / 'hidden.pdf').write_bytes(b'pdf')
    plan = prepare(tmp_path, ' &#x20;\r\n A&amp;B.PDF&#x20;\nA&B.PDF\na&b.PDF\nhidden.pdf\n../x.pdf\nCON.pdf\na.txt')
    assert [e.line for e in plan.entries] == list(range(2, 9))
    assert [e.status for e in plan.entries] == [S.READY, S.DUPLICATE, S.MISSING, S.MISSING, S.INVALID, S.INVALID, S.INVALID]
    assert not plan.output_dir.exists()


def test_numbering_metadata_and_unrelated_files(tmp_path):
    for name in ('会社.pdf', '会社 (1).pdf', 'new.pdf'):
        (tmp_path / name).write_bytes(name.encode())
    output = tmp_path / '対象pdf'
    output.mkdir()
    (output / '会社.pdf').write_bytes(b'old')
    (output / 'unrelated.pdf').write_bytes(b'keep')
    plan = prepare(tmp_path, '会社.pdf\n会社 (1).pdf\nnew.pdf\nmissing.pdf')
    decisions = resolve_decisions(plan, {1: False})
    assert decisions[0].destination == '会社 (2).pdf'
    result = commit_pdf_copy(plan, decisions)
    assert [e.status for e in result.entries] == [S.RENAMED, S.NEW, S.NEW, S.MISSING]
    assert (output / '会社.pdf').read_bytes() == b'old'
    assert (output / 'unrelated.pdf').read_bytes() == b'keep'
    assert (output / 'new.pdf').stat().st_mtime_ns == (tmp_path / 'new.pdf').stat().st_mtime_ns


def test_partial_copy_error_preserves_existing(tmp_path, monkeypatch):
    import job_comparison_app.pdf_copy as service
    (tmp_path / 'a.pdf').write_bytes(b'new')
    (tmp_path / 'b.pdf').write_bytes(b'b')
    output = tmp_path / '対象pdf'
    output.mkdir()
    (output / 'a.pdf').write_bytes(b'old')
    plan = prepare(tmp_path, 'a.pdf\nb.pdf')
    original = service.shutil.copy2
    def fail_first(source, target):
        if source.name == 'a.pdf':
            target.write_bytes(b'partial')
            raise PermissionError('simulated')
        return original(source, target)
    monkeypatch.setattr(service.shutil, 'copy2', fail_first)
    result = commit_pdf_copy(plan, resolve_decisions(plan, {1: True}))
    assert [e.status for e in result.entries] == [S.FAILED, S.NEW]
    assert (output / 'a.pdf').read_bytes() == b'old'
    assert not list(output.glob('.pdf-copy-*'))


@pytest.mark.parametrize('change', ['source', 'destination'])
def test_changed_after_preview_stops_before_writes(tmp_path, change):
    source = tmp_path / 'a.pdf'
    source.write_bytes(b'old')
    plan = prepare(tmp_path, 'a.pdf')
    if change == 'source':
        source.write_bytes(b'changed')
    else:
        plan.output_dir.mkdir()
        (plan.output_dir / 'a.pdf').write_bytes(b'appeared')
    with pytest.raises(InputChangedError):
        commit_pdf_copy(plan, resolve_decisions(plan, {}))


@pytest.mark.parametrize('text', ['', 'missing.pdf', '../bad.pdf'])
def test_no_targets_does_not_create_folder(tmp_path, text):
    plan = prepare(tmp_path, text)
    commit_pdf_copy(plan, resolve_decisions(plan, {}))
    assert not plan.output_dir.exists()


def test_directory_collision_and_output_file(tmp_path):
    (tmp_path / 'a.pdf').write_bytes(b'a')
    output = tmp_path / '対象pdf'
    output.mkdir()
    (output / 'a.pdf').mkdir()
    assert prepare(tmp_path, 'a.pdf').entries[0].status == S.FAILED
    (output / 'a.pdf').rmdir()
    output.rmdir()
    output.write_bytes(b'blocked')
    with pytest.raises(InputValidationError):
        prepare(tmp_path, 'a.pdf')


def test_overwrite_and_preexisting_alias(tmp_path):
    (tmp_path / 'a.pdf').write_bytes(b'new')
    output = tmp_path / '対象pdf'
    output.mkdir()
    (output / 'A.PDF').write_bytes(b'old')
    plan = prepare(tmp_path, 'a.pdf')
    result = commit_pdf_copy(plan, resolve_decisions(plan, {1: True}))
    assert result.entries[0].status == S.REPLACED
    assert (output / 'A.PDF').read_bytes() == b'new'


class ImmediateRunner:
    def submit(self, callback):
        future = Future()
        try:
            future.set_result(callback())
        except Exception as exc:
            future.set_exception(exc)
        return future
    def shutdown(self):
        pass


@pytest.mark.parametrize('cancel', ['conflict', 'final', 'none'])
def test_controller_confirmation_and_busy(tmp_path, cancel):
    (tmp_path / 'a.pdf').write_bytes(b'new')
    output = tmp_path / '対象pdf'
    output.mkdir()
    (output / 'a.pdf').write_bytes(b'old')
    panel = Mock()
    panel.request.return_value = PdfCopyRequest(tmp_path, 'a.pdf')
    panel.ask_conflict.return_value = OutputConflictChoice.CANCEL if cancel == 'conflict' else OutputConflictChoice.REPLACE
    panel.ask_commit.return_value = cancel != 'final'
    view = Mock(copy_panel=panel, recruit_panel=None)
    view.post.side_effect = lambda callback: callback()
    controller = Controller(view=view, application=JobComparisonApplication(), output_opener=Mock(), task_runner=ImmediateRunner())
    controller.apply_copy()
    assert not controller.model.busy
    assert (output / 'a.pdf').read_bytes() == (b'new' if cancel == 'none' else b'old')


def test_panel_edit_invalidation_and_copy_flow(tk_window, tmp_path):
    view = TkView(tk_window)
    controller = Controller(view=view, application=JobComparisonApplication(), output_opener=Mock(), task_runner=ImmediateRunner())
    view.controller = controller
    view.post = lambda callback: callback()
    (tmp_path / '会社.pdf').write_bytes(b'pdf')
    view.copy_panel.folder.set(str(tmp_path))
    view.copy_panel.editor.insert('1.0', '会社.pdf')
    tk_window.update()
    controller.preview_copy()
    assert controller.model.copy_plan is not None
    view.copy_panel.editor.insert('end', '\nmissing.pdf')
    tk_window.update()
    assert controller.model.copy_plan is None
    view.copy_panel.ask_commit = lambda *args: True
    controller.apply_copy()
    assert controller.model.copy_result.warning
    assert len(view.copy_panel.tree.get_children()) == 2
    assert (tmp_path / '対象pdf' / '会社.pdf').read_bytes() == b'pdf'
    assert view.notebook.tab(4, 'text') == '対象PDFコピー'
    assert view.notebook.tab(5, 'text') == '比較資料PDF生成'


def test_race_at_publication_does_not_overwrite(tmp_path, monkeypatch):
    import job_comparison_app.pdf_copy as service
    (tmp_path / 'a.pdf').write_bytes(b'source')
    plan = prepare(tmp_path, 'a.pdf')
    original = service.os.rename
    def competing_writer(source, target):
        target.write_bytes(b'other-writer')
        original(source, target)
    monkeypatch.setattr(service.os, 'rename', competing_writer)
    result = commit_pdf_copy(plan, resolve_decisions(plan, {}))
    assert result.entries[0].status == S.FAILED
    assert (plan.output_dir / 'a.pdf').read_bytes() == b'other-writer'
    assert not list(plan.output_dir.glob('.pdf-copy-*'))


@pytest.mark.parametrize('location', ['source', 'file', 'directory'])
def test_link_paths_are_rejected(tmp_path, monkeypatch, location):
    import job_comparison_app.pdf_copy as service
    source = tmp_path / 'a.pdf'
    source.write_bytes(b'a')
    output = tmp_path / '対象pdf'
    output.mkdir()
    target = output / 'a.pdf'
    target.write_bytes(b'old')
    linked_path = {'source': source, 'file': target, 'directory': output}[location]
    monkeypatch.setattr(service, 'linked', lambda path: path == linked_path)
    if location == 'directory':
        with pytest.raises(InputValidationError):
            prepare(tmp_path, 'a.pdf')
    else:
        assert prepare(tmp_path, 'a.pdf').entries[0].status == S.FAILED
    assert target.read_bytes() == b'old'


def test_copy_scroll_routes_and_undo(tk_window, tmp_path):
    view = TkView(tk_window)
    controller = Mock()
    view.controller = controller
    view._select_top_tab(4)
    tk_window.geometry('760x600')
    tk_window.deiconify()
    view.copy_panel.editor.insert('1.0', '\n'.join(f'{i}.pdf' for i in range(100)))
    tk_window.update()
    canvas = view._tab_canvases[4]
    before = canvas.yview()
    assert view._route_copy_mousewheel(SimpleNamespace(widget=view.copy_tab, delta=-120)) == 'break'
    assert canvas.yview()[0] > before[0]
    before = canvas.yview()
    assert view.copy_panel.wheel(SimpleNamespace(delta=-120)) == 'break'
    assert canvas.yview() == before
    view.copy_panel.editor.edit_separator()
    view.copy_panel.editor.insert('end', '\nextra.pdf')
    tk_window.update()
    controller.invalidate_copy.reset_mock()
    view.copy_panel.editor.edit_undo()
    tk_window.update()
    controller.invalidate_copy.assert_called()


@pytest.mark.parametrize('action', ['置換', '別名保存', 'キャンセル', 'close', 'escape'])
def test_real_conflict_dialog_choices(tk_window, tmp_path, action):
    import tkinter as tk
    from tkinter import ttk
    view = TkView(tk_window)
    (tmp_path / 'a.pdf').write_bytes(b'a')
    output = tmp_path / '対象pdf'
    output.mkdir()
    (output / 'a.pdf').write_bytes(b'old')
    entry = prepare(tmp_path, 'a.pdf').entries[0]
    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)
    def choose():
        dialogs = [w for w in walk(tk_window) if isinstance(w, tk.Toplevel) and w != tk_window]
        if not dialogs:
            tk_window.after(20, choose)
            return
        dialog = dialogs[-1]
        if action == 'close':
            dialog.tk.call(dialog.protocol('WM_DELETE_WINDOW'))
        elif action == 'escape':
            dialog.focus_force()
            dialog.event_generate('<Escape>')
        else:
            next(w for w in walk(dialog) if isinstance(w, ttk.Button) and w.cget('text') == action).invoke()
    tk_window.after(50, choose)
    selected = view.copy_panel.ask_conflict(entry, 1, 1, output)
    assert selected == {'置換': OutputConflictChoice.REPLACE, '別名保存': OutputConflictChoice.SAVE_AS_NEW}.get(action, OutputConflictChoice.CANCEL)
    assert (output / 'a.pdf').read_bytes() == b'old'
