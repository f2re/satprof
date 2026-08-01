from __future__ import annotations
import html
import json
import pandas as pd
import matplotlib.pyplot as plt


def generate_report(workspace, instrument: str, pairs: pd.DataFrame | None = None, corrected: pd.DataFrame | None = None, bias_metrics: dict | None = None, retrieval_metrics: dict | None = None):
    report_dir=workspace.root/'reports'/'latest'; report_dir.mkdir(parents=True,exist_ok=True); images=[]
    if pairs is not None and not pairs.empty:
        stat=pairs.groupby('channel').innovation_k.agg(['count','mean','std']).reset_index(); fig,ax=plt.subplots(figsize=(10,4)); ax.errorbar(stat.channel.astype(str),stat['mean'],yerr=stat['std'],fmt='o'); ax.axhline(0,linewidth=1); ax.set_xlabel('Канал'); ax.set_ylabel('O−B, K'); ax.set_title('Исходные инновации'); fig.tight_layout(); p=report_dir/'bias_raw.png'; fig.savefig(p,dpi=140); plt.close(fig); images.append(('Исходные O−B',p.name))
    if corrected is not None and not corrected.empty:
        stat=corrected.groupby('channel').corrected_innovation_k.agg(['count','mean','std']).reset_index(); fig,ax=plt.subplots(figsize=(10,4)); ax.errorbar(stat.channel.astype(str),stat['mean'],yerr=stat['std'],fmt='o'); ax.axhline(0,linewidth=1); ax.set_xlabel('Канал'); ax.set_ylabel('O−B после поправки, K'); ax.set_title('Остаточные инновации'); fig.tight_layout(); p=report_dir/'bias_corrected.png'; fig.savefig(p,dpi=140); plt.close(fig); images.append(('O−B после поправки',p.name))
    sections=[]
    if bias_metrics: sections.append('<h2>Поправки</h2><pre>'+html.escape(json.dumps(bias_metrics,ensure_ascii=False,indent=2))+'</pre>')
    if retrieval_metrics: sections.append('<h2>Восстановление профиля</h2><pre>'+html.escape(json.dumps(retrieval_metrics,ensure_ascii=False,indent=2))+'</pre>')
    imgs=''.join(f'<h2>{html.escape(title)}</h2><img src="{name}" style="max-width:100%">' for title,name in images)
    page=f"""<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>SatProf — {html.escape(instrument)}</title><style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:30px auto;padding:0 20px;line-height:1.45}}pre{{background:#f4f4f4;padding:16px;overflow:auto}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:6px}}</style></head><body><h1>SatProf: {html.escape(instrument)}</h1>{imgs}{''.join(sections)}</body></html>"""
    (report_dir/'index.html').write_text(page,encoding='utf-8'); return report_dir/'index.html'
