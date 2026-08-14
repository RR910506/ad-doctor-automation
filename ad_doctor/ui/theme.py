TOKENS={"ink":"#16202A","muted":"#667085","surface":"#FFFFFF","canvas":"#F6F7F9","border":"#E4E7EC","primary":"#B42318","success":"#067647","warning":"#B54708","error":"#B42318","neutral":"#475467","radius":"14px"}
CSS=f"""
<style>
:root{{--ad-ink:{TOKENS['ink']};--ad-muted:{TOKENS['muted']};--ad-surface:{TOKENS['surface']};--ad-canvas:{TOKENS['canvas']};--ad-border:{TOKENS['border']};--ad-primary:{TOKENS['primary']};}}
.stApp{{background:var(--ad-canvas);color:var(--ad-ink)}}
.block-container{{max-width:1440px;padding-top:2rem;padding-bottom:4rem}}
h1,h2,h3{{letter-spacing:-.025em;color:var(--ad-ink)}}
.ad-kicker{{font-size:.76rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ad-primary)}}
.ad-page-copy,.ad-muted{{color:var(--ad-muted);line-height:1.55}}
.ad-card{{background:var(--ad-surface);border:1px solid var(--ad-border);border-radius:14px;padding:1rem 1.1rem;margin:.45rem 0}}
.ad-row{{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start}}
.ad-badge{{display:inline-flex;align-items:center;border-radius:999px;padding:.2rem .62rem;font-size:.78rem;font-weight:650;background:#F2F4F7;color:#344054}}
.ad-badge.success{{background:#ECFDF3;color:#067647}}.ad-badge.warning{{background:#FFFAEB;color:#B54708}}.ad-badge.error{{background:#FEF3F2;color:#B42318}}.ad-badge.info{{background:#EFF8FF;color:#175CD3}}
.ad-empty{{text-align:center;border:1px dashed #D0D5DD;border-radius:14px;padding:2.2rem;background:#fff}}
.ad-skeleton{{height:5rem;border-radius:14px;background:#EAECF0;animation:pulse 1.4s ease-in-out infinite}}@keyframes pulse{{50%{{opacity:.45}}}}
[data-testid="stMetric"]{{background:var(--ad-surface);border:1px solid var(--ad-border);border-radius:14px;padding:.85rem 1rem}}
[data-testid="stMetricLabel"],[data-testid="stMetricValue"]{{color:var(--ad-ink)!important}}
[data-testid="stMain"] [data-testid="stWidgetLabel"] p,[data-testid="stMain"] [role="radiogroup"] label p{{color:var(--ad-ink)!important}}
[data-testid="stMain"] button[kind="secondary"]{{background:var(--ad-surface)!important;border-color:var(--ad-border)!important}}
[data-testid="stMain"] button[kind="secondary"] p{{color:var(--ad-ink)!important}}
[data-testid="stMain"] [data-testid="stTabs"] button p{{color:var(--ad-ink)!important}}
[data-testid="stMain"] [data-testid="stAlert"] p{{color:var(--ad-ink)!important}}
[data-testid="stMain"] button:disabled{{opacity:.62}}
:focus-visible{{outline:3px solid #84CAFF!important;outline-offset:2px!important}}
@media(max-width:1100px){{[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has([data-testid="stTextInput"]),[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]){{flex-wrap:wrap}}[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has([data-testid="stTextInput"])>[data-testid="stColumn"],[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])>[data-testid="stColumn"]{{min-width:calc(50% - 1rem)!important;flex:1 1 calc(50% - 1rem)!important}}}}
@media(max-width:760px){{.block-container{{padding:1rem}}.ad-row{{display:block}}h1{{font-size:1.75rem!important}}[data-testid="stMain"] [data-testid="stHorizontalBlock"]>[data-testid="stColumn"],[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])>[data-testid="stColumn"],[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has([data-testid="stTextInput"])>[data-testid="stColumn"]{{min-width:100%!important;flex:1 1 100%!important}}}}
</style>"""
def apply_theme(st):st.markdown(CSS,unsafe_allow_html=True)
