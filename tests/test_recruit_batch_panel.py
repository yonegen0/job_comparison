from pathlib import Path
from unittest.mock import patch

from job_comparison_app.gui.view import TkView
from recruit_job_tool.application import ProviderHealth, ProviderStatus
from recruit_job_tool.gui.controller import GlobalPhase, ProviderViewModel, ViewModel
from recruit_job_tool.models import JobSource


def test_panel_shows_five_paths_and_disambiguates_same_names(tk_window):
    view = TkView(tk_window)
    panel = view.recruit_panel
    paths = (Path("C:/first/同名.html"), Path("C:/second/同名.html"),
             Path("C:/first/長い日本語ファイル名" + "検索結果" * 30 + ".html"),
             Path("C:/first/4.html"), Path("C:/first/5.html"))
    providers = {
        p: ProviderViewModel(p, selected_paths=paths if p is JobSource.JOBINS else (),
                            status=ProviderStatus(p, ProviderHealth.READY, 3, True, True, False, True, False))
        for p in JobSource
    }
    panel.render(ViewModel(GlobalPhase.IDLE, JobSource.JOBINS, providers, "ready", False))
    assert panel._paths[JobSource.JOBINS].get() == "選択済み：5 / 5個"
    listing = panel._path_lists[JobSource.JOBINS]
    assert listing.size() == 5
    assert str(paths[0].parent) in listing.get(0)
    assert str(paths[1].parent) in listing.get(1)
    assert listing.cget("xscrollcommand")
    assert panel._paths[JobSource.CAREERBANK].get() == "HTML未選択"
    assert str(panel._controls[JobSource.CAREERBANK]["import_"].cget("state")) == "disabled"
    assert str(panel._controls[JobSource.JOBINS]["import_"].cget("state")) == "normal"
    with patch("job_comparison_app.gui.recruit_feature.filedialog.askopenfilenames", return_value=tuple(map(str, paths))) as picker:
        assert panel.choose_html(initial_directory=Path("C:/first")) == paths
    assert "最大5個" in picker.call_args.kwargs["title"]
    with patch("job_comparison_app.gui.recruit_feature.filedialog.askopenfilenames", return_value=()):
        assert panel.choose_html(initial_directory=None) == ()
