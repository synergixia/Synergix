/**
 * synergix-i18n.js
 *
 * Translation table for the new on-chain tool widgets.  Decoupled
 * from the page-wide `T` object so we can ship the widgets without
 * touching the existing translation block.
 *
 * The widget root listens for clicks on the existing `.lb`
 * language buttons and to a `synergix:langchange` custom event,
 * and re-renders any [data-st-i18n] node beneath it.
 *
 * Languages mirror the page (es/en/pt/fr/de/zh/ja/ko/ar/hi/...).
 * Unknown codes fall back to English.
 */

const STRINGS = {
  /* ── Live stats (hero) ─────────────────────────────────────── */
  st_stats_title: {
    es: 'Estado en vivo on-chain',
    en: 'Live on-chain status',
    pt: 'Status on-chain ao vivo',
    fr: 'État en direct on-chain',
    de: 'Live On-Chain-Status',
    zh: '实时链上状态',
    ja: 'ライブオンチェーンステータス',
    ko: '실시간 온체인 상태',
    ar: 'الحالة المباشرة على السلسلة',
    hi: 'लाइव ऑन-चेन स्थिति',
    bn: 'লাইভ অন-চেইন স্ট্যাটাস',
    id: 'Status on-chain langsung',
    ur: 'لائیو آن چین صورتحال',
  },
  st_stats_sub: {
    es: 'Datos leídos directamente de Irys, BSC y DexScreener — sin servidor de Synergix.',
    en: 'Data read directly from Irys, BSC and DexScreener — no Synergix server in the loop.',
    pt: 'Dados lidos diretamente do Irys, BSC e DexScreener — sem servidor Synergix.',
    fr: 'Données lues directement depuis Irys, BSC et DexScreener — sans serveur Synergix.',
    de: 'Daten direkt aus Irys, BSC und DexScreener — ohne Synergix-Server.',
    zh: '数据直接读取自Irys、BSC和DexScreener — 无需Synergix服务器。',
    ja: 'Irys、BSC、DexScreenerから直接読み取り — Synergixサーバーは不要。',
    ko: 'Irys, BSC, DexScreener에서 직접 읽음 — Synergix 서버 없음.',
    ar: 'البيانات تُقرأ مباشرة من Irys وBSC وDexScreener — بدون خادم Synergix.',
    hi: 'Irys, BSC और DexScreener से सीधे डेटा — कोई Synergix सर्वर नहीं।',
    bn: 'Irys, BSC এবং DexScreener থেকে সরাসরি ডেটা — কোনো Synergix সার্ভার নেই।',
    id: 'Data dibaca langsung dari Irys, BSC dan DexScreener — tanpa server Synergix.',
    ur: 'Irys، BSC اور DexScreener سے براہ راست ڈیٹا — Synergix سرور نہیں۔',
  },
  st_price: { es: 'Precio', en: 'Price', pt: 'Preço', fr: 'Prix', de: 'Preis', zh: '价格', ja: '価格', ko: '가격', ar: 'السعر', hi: 'मूल्य', bn: 'মূল্য', id: 'Harga', ur: 'قیمت' },
  st_change_24h: { es: '24h', en: '24h', pt: '24h', fr: '24h', de: '24h', zh: '24小时', ja: '24時間', ko: '24시간', ar: '24س', hi: '24घं', bn: '24ঘ', id: '24j', ur: '24گھ' },
  st_volume: { es: 'Volumen 24h', en: 'Volume 24h', pt: 'Volume 24h', fr: 'Volume 24h', de: 'Volumen 24h', zh: '24小时量', ja: '24時間出来高', ko: '24시간 거래량', ar: 'الحجم 24س', hi: '24घं वॉल्यूम', bn: '24ঘ ভলিউম', id: 'Volume 24j', ur: '24گھ والیوم' },
  st_liquidity: { es: 'Liquidez', en: 'Liquidity', pt: 'Liquidez', fr: 'Liquidité', de: 'Liquidität', zh: '流动性', ja: '流動性', ko: '유동성', ar: 'السيولة', hi: 'तरलता', bn: 'তরলতা', id: 'Likuiditas', ur: 'لیکویڈیٹی' },
  st_marketcap: { es: 'Market Cap', en: 'Market Cap', pt: 'Cap. de mercado', fr: 'Cap. de marché', de: 'Marktkapital.', zh: '市值', ja: '時価総額', ko: '시총', ar: 'القيمة السوقية', hi: 'मार्केट कैप', bn: 'মার্কেট ক্যাপ', id: 'Kap. Pasar', ur: 'مارکیٹ کیپ' },
  st_curve_title: {
    es: 'Curva de bonding (four.meme)',
    en: 'Bonding curve (four.meme)',
    pt: 'Curva bonding (four.meme)',
    fr: 'Courbe de bonding (four.meme)',
    de: 'Bonding-Kurve (four.meme)',
    zh: '联合曲线 (four.meme)',
    ja: 'ボンディングカーブ (four.meme)',
    ko: '본딩 커브 (four.meme)',
    ar: 'منحنى الترابط (four.meme)',
    hi: 'बॉन्डिंग कर्व (four.meme)',
    bn: 'বন্ডিং কার্ভ (four.meme)',
    id: 'Kurva bonding (four.meme)',
    ur: 'بانڈنگ کرو (four.meme)',
  },
  st_curve_graduated: {
    es: '✅ Graduado a PancakeSwap',
    en: '✅ Graduated to PancakeSwap',
    pt: '✅ Graduado para PancakeSwap',
    fr: '✅ Diplômé sur PancakeSwap',
    de: '✅ Auf PancakeSwap migriert',
    zh: '✅ 已迁移到PancakeSwap',
    ja: '✅ PancakeSwapに移行',
    ko: '✅ PancakeSwap로 졸업',
    ar: '✅ تخرّج إلى PancakeSwap',
    hi: '✅ PancakeSwap में स्नातक',
    bn: '✅ PancakeSwap এ গ্র্যাজুয়েট',
    id: '✅ Lulus ke PancakeSwap',
    ur: '✅ PancakeSwap پر منتقل',
  },
  st_loading: { es: 'Cargando…', en: 'Loading…', pt: 'Carregando…', fr: 'Chargement…', de: 'Lade…', zh: '加载中…', ja: '読み込み中…', ko: '로딩…', ar: 'جارٍ التحميل…', hi: 'लोड हो रहा है…', bn: 'লোড হচ্ছে…', id: 'Memuat…', ur: 'لوڈ ہو رہا ہے…' },
  st_unavailable: { es: 'No disponible', en: 'Unavailable', pt: 'Indisponível', fr: 'Indisponible', de: 'Nicht verfügbar', zh: '不可用', ja: '利用不可', ko: '사용 불가', ar: 'غير متاح', hi: 'अनुपलब्ध', bn: 'অনুপলব্ধ', id: 'Tidak tersedia', ur: 'دستیاب نہیں' },

  /* ── Memory Explorer ───────────────────────────────────────── */
  mx_eye: {
    es: 'Memoria Inmortal en vivo',
    en: 'Live Immortal Memory',
    pt: 'Memória Imortal ao vivo',
    fr: 'Mémoire Immortelle en direct',
    de: 'Live Unsterbliches Gedächtnis',
    zh: '实时不朽记忆',
    ja: 'ライブ不死のメモリ',
    ko: '실시간 불멸의 기억',
    ar: 'الذاكرة الخالدة المباشرة',
    hi: 'लाइव अमर स्मृति',
    bn: 'লাইভ অমর স্মৃতি',
    id: 'Memori Abadi langsung',
    ur: 'لائیو امر یاد',
  },
  mx_title: {
    es: 'Explora el cerebro on-chain',
    en: 'Explore the on-chain brain',
    pt: 'Explore o cérebro on-chain',
    fr: 'Explorez le cerveau on-chain',
    de: 'On-Chain-Gehirn erkunden',
    zh: '探索链上大脑',
    ja: 'オンチェーン脳を探索',
    ko: '온체인 두뇌 탐색',
    ar: 'استكشف الدماغ على السلسلة',
    hi: 'ऑन-चेन मस्तिष्क खोजें',
    bn: 'অন-চেইন মস্তিষ্ক অন্বেষণ',
    id: 'Jelajahi otak on-chain',
    ur: 'آن چین دماغ دریافت',
  },
  mx_sub: {
    es: 'Cada tarjeta abajo es un aporte permanente sellado en Irys. Tu navegador lee la blockchain directamente — nada pasa por ningún servidor.',
    en: 'Every card below is a permanent contribution sealed on Irys. Your browser reads the blockchain directly — nothing passes through any server.',
    pt: 'Cada cartão abaixo é uma contribuição permanente selada no Irys. Seu navegador lê a blockchain diretamente — nada passa por servidor.',
    fr: "Chaque carte ci-dessous est une contribution permanente scellée sur Irys. Votre navigateur lit la blockchain directement — rien ne passe par un serveur.",
    de: 'Jede Karte unten ist ein dauerhafter Beitrag, der auf Irys gespeichert ist. Ihr Browser liest die Blockchain direkt — kein Server dazwischen.',
    zh: '下方每张卡片都是永久封存在Irys上的贡献。浏览器直接读取区块链 — 没有服务器中介。',
    ja: '下の各カードはIrysに永続的に封印された貢献です。ブラウザがブロックチェーンを直接読み取ります — サーバー経由はありません。',
    ko: '아래 각 카드는 Irys에 영구 봉인된 기여입니다. 브라우저가 블록체인을 직접 읽습니다 — 서버 경유 없음.',
    ar: 'كل بطاقة أدناه هي مساهمة دائمة مُختَمة على Irys. متصفحك يقرأ البلوكشين مباشرة — لا يمر شيء عبر أي خادم.',
    hi: 'नीचे प्रत्येक कार्ड Irys पर सील किया गया स्थायी योगदान है। आपका ब्राउज़र ब्लॉकचेन को सीधे पढ़ता है — कोई सर्वर नहीं।',
    bn: 'নিচের প্রতিটি কার্ড Irys-এ স্থায়ীভাবে সিল করা একটি অবদান। আপনার ব্রাউজার সরাসরি ব্লকচেইন পড়ছে — কোনো সার্ভার নেই।',
    id: 'Setiap kartu di bawah adalah kontribusi permanen yang disegel di Irys. Browser Anda membaca blockchain langsung — tanpa server.',
    ur: 'نیچے ہر کارڈ Irys پر مہر بند ایک مستقل شراکت ہے۔ آپ کا براؤزر بلاک چین کو براہ راست پڑھتا ہے — کوئی سرور نہیں۔',
  },
  mx_filter_all: { es: 'Todas', en: 'All', pt: 'Todas', fr: 'Toutes', de: 'Alle', zh: '全部', ja: 'すべて', ko: '전체', ar: 'الكل', hi: 'सभी', bn: 'সব', id: 'Semua', ur: 'تمام' },
  mx_view_raw: { es: 'Ver original', en: 'View raw', pt: 'Ver original', fr: 'Voir original', de: 'Original', zh: '查看原文', ja: '原文', ko: '원문 보기', ar: 'عرض الأصل', hi: 'मूल देखें', bn: 'মূল দেখুন', id: 'Lihat asli', ur: 'اصل دیکھیں' },
  mx_loading: { es: 'Leyendo de la blockchain…', en: 'Reading from the blockchain…', pt: 'Lendo da blockchain…', fr: 'Lecture depuis la blockchain…', de: 'Lese aus Blockchain…', zh: '从区块链读取…', ja: 'ブロックチェーンから読み取り中…', ko: '블록체인에서 읽는 중…', ar: 'القراءة من البلوكشين…', hi: 'ब्लॉकचेन से पढ़ रहा है…', bn: 'ব্লকচেইন থেকে পড়া হচ্ছে…', id: 'Membaca dari blockchain…', ur: 'بلاک چین سے پڑھ رہا ہے…' },
  mx_empty: {
    es: 'Aún no hay aportes en este filtro.',
    en: 'No contributions match this filter yet.',
    pt: 'Ainda não há contribuições com este filtro.',
    fr: 'Aucune contribution avec ce filtre.',
    de: 'Noch keine Beiträge in diesem Filter.',
    zh: '此筛选下暂无贡献。',
    ja: 'このフィルターに該当する貢献はまだありません。',
    ko: '이 필터에 해당하는 기여가 아직 없습니다.',
    ar: 'لا توجد مساهمات بهذا الفلتر بعد.',
    hi: 'इस फ़िल्टर में अभी कोई योगदान नहीं।',
    bn: 'এই ফিল্টারে এখনো কোনো অবদান নেই।',
    id: 'Belum ada kontribusi dengan filter ini.',
    ur: 'اس فلٹر میں ابھی کوئی شراکت نہیں۔',
  },
  mx_error: {
    es: 'No se pudo leer la Memoria Inmortal. Reintenta.',
    en: 'Could not read Immortal Memory. Retry.',
    pt: 'Não foi possível ler a Memória Imortal. Tente de novo.',
    fr: "Impossible de lire la Mémoire Immortelle. Réessayez.",
    de: 'Unsterbliches Gedächtnis konnte nicht gelesen werden. Erneut versuchen.',
    zh: '无法读取不朽记忆。请重试。',
    ja: '不死のメモリを読み取れませんでした。再試行してください。',
    ko: '불멸의 기억을 읽을 수 없습니다. 재시도.',
    ar: 'تعذر قراءة الذاكرة الخالدة. أعد المحاولة.',
    hi: 'अमर स्मृति नहीं पढ़ सके। पुनः प्रयास करें।',
    bn: 'অমর স্মৃতি পড়া যায়নি। পুনরায় চেষ্টা করুন।',
    id: 'Tidak dapat membaca Memori Abadi. Coba lagi.',
    ur: 'امر یاد نہیں پڑھی جا سکی۔ دوبارہ کوشش کریں۔',
  },
  mx_quality: { es: 'Calidad', en: 'Quality', pt: 'Qualidade', fr: 'Qualité', de: 'Qualität', zh: '质量', ja: '品質', ko: '품질', ar: 'الجودة', hi: 'गुणवत्ता', bn: 'গুণমান', id: 'Kualitas', ur: 'معیار' },
  mx_loadmore: { es: 'Cargar más', en: 'Load more', pt: 'Carregar mais', fr: 'Charger plus', de: 'Mehr laden', zh: '加载更多', ja: 'もっと読み込む', ko: '더 보기', ar: 'تحميل المزيد', hi: 'और लोड करें', bn: 'আরও লোড', id: 'Muat lainnya', ur: 'مزید لوڈ' },

  /* ── Top Minds ─────────────────────────────────────────────── */
  tm_eye: {
    es: 'Reputación on-chain',
    en: 'On-chain reputation',
    pt: 'Reputação on-chain',
    fr: 'Réputation on-chain',
    de: 'On-Chain-Reputation',
    zh: '链上声誉',
    ja: 'オンチェーン評判',
    ko: '온체인 평판',
    ar: 'السمعة على السلسلة',
    hi: 'ऑन-चेन प्रतिष्ठा',
    bn: 'অন-চেইন খ্যাতি',
    id: 'Reputasi on-chain',
    ur: 'آن چین ساکھ',
  },
  tm_title: {
    es: 'Top mentes de la red',
    en: 'Top minds of the network',
    pt: 'Top mentes da rede',
    fr: "Top esprits du réseau",
    de: 'Top-Köpfe des Netzwerks',
    zh: '网络顶级思想者',
    ja: 'ネットワークのトップマインド',
    ko: '네트워크 최고의 마인드',
    ar: 'أفضل العقول في الشبكة',
    hi: 'नेटवर्क के शीर्ष मस्तिष्क',
    bn: 'নেটওয়ার্কের শীর্ষ মন',
    id: 'Pikiran teratas jaringan',
    ur: 'نیٹ ورک کے بہترین اذہان',
  },
  tm_sub: {
    es: 'Ranking calculado leyendo perfiles y aportes directamente de Irys. Cada subida de rango está sellada on-chain.',
    en: 'Ranking computed by reading profiles and contributions straight from Irys. Every rank-up is sealed on-chain.',
    pt: 'Ranking calculado lendo perfis e contribuições direto do Irys. Cada subida de rank é selada on-chain.',
    fr: "Classement calculé en lisant profils et contributions directement depuis Irys. Chaque montée de rang est scellée on-chain.",
    de: 'Ranking berechnet aus Profilen und Beiträgen direkt von Irys. Jede Rangerhöhung ist on-chain versiegelt.',
    zh: '直接从Irys读取档案和贡献计算的排名。每次升级都已链上封存。',
    ja: 'プロフィールと貢献をIrysから直接読み取って計算したランキング。すべてのランクアップはオンチェーンで封印されています。',
    ko: 'Irys에서 직접 프로필과 기여를 읽어 계산한 순위. 모든 등급 상승은 온체인에 봉인됩니다.',
    ar: 'ترتيب محسوب بقراءة الملفات والمساهمات مباشرة من Irys. كل ترقية مختومة على السلسلة.',
    hi: 'प्रोफाइल और योगदान सीधे Irys से पढ़कर गणना की गई रैंकिंग। हर रैंक-अप ऑन-चेन सील है।',
    bn: 'Irys থেকে সরাসরি প্রোফাইল ও অবদান পড়ে গণনা করা র্যাঙ্কিং। প্রতিটি র্যাঙ্ক-আপ অন-চেইনে সিল।',
    id: 'Peringkat dihitung dengan membaca profil dan kontribusi langsung dari Irys. Setiap kenaikan rank disegel on-chain.',
    ur: 'پروفائلز اور شراکتوں کو براہ راست Irys سے پڑھ کر شمار کیا گیا درجہ بندی۔ ہر رینک اپ آن چین مہر بند ہے۔',
  },
  tm_col_rank: { es: 'Rango', en: 'Rank', pt: 'Rank', fr: 'Rang', de: 'Rang', zh: '排名', ja: 'ランク', ko: '순위', ar: 'الترتيب', hi: 'रैंक', bn: 'র্যাঙ্ক', id: 'Peringkat', ur: 'درجہ' },
  tm_col_who: { es: 'Mente', en: 'Mind', pt: 'Mente', fr: 'Esprit', de: 'Kopf', zh: '思想', ja: 'マインド', ko: '마인드', ar: 'عقل', hi: 'मस्तिष्क', bn: 'মন', id: 'Pikiran', ur: 'ذہن' },
  tm_col_points: { es: 'Puntos', en: 'Points', pt: 'Pontos', fr: 'Points', de: 'Punkte', zh: '点数', ja: 'ポイント', ko: '포인트', ar: 'نقاط', hi: 'अंक', bn: 'পয়েন্ট', id: 'Poin', ur: 'پوائنٹس' },
  tm_col_aportes: { es: 'Aportes', en: 'Contribs', pt: 'Aportes', fr: 'Aports', de: 'Beiträge', zh: '贡献', ja: '貢献', ko: '기여', ar: 'مساهمات', hi: 'योगदान', bn: 'অবদান', id: 'Kontribusi', ur: 'شراکتیں' },
  tm_col_uses: { es: 'Usos por la IA', en: 'AI uses', pt: 'Usos pela IA', fr: 'Usages IA', de: 'KI-Nutzungen', zh: 'AI使用', ja: 'AI利用', ko: 'AI 사용', ar: 'استخدامات الذكاء', hi: 'AI उपयोग', bn: 'AI ব্যবহার', id: 'Pemakaian AI', ur: 'AI استعمال' },
  tm_loading: { es: 'Calculando ranking…', en: 'Computing ranking…', pt: 'Calculando ranking…', fr: 'Calcul du classement…', de: 'Berechne Ranking…', zh: '计算排名…', ja: 'ランキング計算中…', ko: '순위 계산 중…', ar: 'حساب الترتيب…', hi: 'रैंकिंग गणना…', bn: 'র্যাঙ্কিং গণনা…', id: 'Menghitung peringkat…', ur: 'درجہ بندی شمار…' },

  /* ── Wallet verification ──────────────────────────────────── */
  wv_eye: {
    es: 'Identidad on-chain',
    en: 'On-chain identity',
    pt: 'Identidade on-chain',
    fr: 'Identité on-chain',
    de: 'On-Chain-Identität',
    zh: '链上身份',
    ja: 'オンチェーンID',
    ko: '온체인 신원',
    ar: 'الهوية على السلسلة',
    hi: 'ऑन-चेन पहचान',
    bn: 'অন-চেইন পরিচয়',
    id: 'Identitas on-chain',
    ur: 'آن چین شناخت',
  },
  wv_title: {
    es: 'Verifica tu wallet',
    en: 'Verify your wallet',
    pt: 'Verifique sua wallet',
    fr: 'Vérifiez votre wallet',
    de: 'Wallet verifizieren',
    zh: '验证你的钱包',
    ja: 'ウォレットを確認',
    ko: '지갑 인증',
    ar: 'تحقق من محفظتك',
    hi: 'अपना वॉलेट सत्यापित करें',
    bn: 'আপনার ওয়ালেট যাচাই',
    id: 'Verifikasi dompet Anda',
    ur: 'اپنے والیٹ کی تصدیق',
  },
  wv_sub: {
    es: 'Firma un mensaje gasless con tu MetaMask para demostrar que controlas la wallet. La firma nunca toca un servidor — la verificación ocurre en tu navegador.',
    en: 'Sign a gasless message with MetaMask to prove you control the wallet. The signature never touches a server — verification happens in your browser.',
    pt: 'Assine uma mensagem gasless com sua MetaMask para provar que controla a wallet. A assinatura nunca toca um servidor — a verificação acontece no seu navegador.',
    fr: "Signez un message sans gaz avec MetaMask pour prouver que vous contrôlez le wallet. La signature ne touche jamais de serveur — la vérification se fait dans votre navigateur.",
    de: 'Signieren Sie eine gasfreie Nachricht mit MetaMask, um zu beweisen, dass Sie die Wallet kontrollieren. Die Signatur erreicht keinen Server — die Verifizierung passiert in Ihrem Browser.',
    zh: '用MetaMask签署免gas消息证明你拥有该钱包。签名不会传到任何服务器 — 验证在你的浏览器中完成。',
    ja: 'MetaMaskでガス不要メッセージに署名してウォレットの所有を証明します。署名はサーバーに送信されず — 検証はブラウザで行われます。',
    ko: 'MetaMask로 가스 없는 메시지에 서명하여 지갑 소유를 증명하세요. 서명은 서버로 전송되지 않으며 — 브라우저에서 검증됩니다.',
    ar: 'وقّع رسالة بدون رسوم باستخدام MetaMask لإثبات تحكمك بالمحفظة. التوقيع لا يصل إلى أي خادم — التحقق يحدث في متصفحك.',
    hi: 'अपनी वॉलेट का स्वामित्व साबित करने के लिए MetaMask से gasless संदेश पर हस्ताक्षर करें। हस्ताक्षर सर्वर तक नहीं पहुँचता — सत्यापन आपके ब्राउज़र में होता है।',
    bn: 'আপনার ওয়ালেট নিয়ন্ত্রণ প্রমাণ করতে MetaMask দিয়ে গ্যাসহীন বার্তায় স্বাক্ষর করুন। স্বাক্ষর কোনো সার্ভারে যায় না — যাচাই আপনার ব্রাউজারে হয়।',
    id: 'Tandatangani pesan gasless dengan MetaMask untuk membuktikan kepemilikan dompet. Tanda tangan tidak menyentuh server — verifikasi terjadi di browser Anda.',
    ur: 'اپنے والیٹ کی ملکیت ثابت کرنے کے لیے MetaMask سے ایک gasless پیغام پر دستخط کریں۔ دستخط کسی سرور تک نہیں پہنچتا — تصدیق آپ کے براؤزر میں ہوتی ہے۔',
  },
  wv_connect: { es: 'Conectar wallet', en: 'Connect wallet', pt: 'Conectar wallet', fr: 'Connecter wallet', de: 'Wallet verbinden', zh: '连接钱包', ja: 'ウォレット接続', ko: '지갑 연결', ar: 'ربط المحفظة', hi: 'वॉलेट कनेक्ट', bn: 'ওয়ালেট সংযোগ', id: 'Hubungkan dompet', ur: 'والیٹ کنیکٹ' },
  wv_sign: { es: 'Firmar mensaje', en: 'Sign message', pt: 'Assinar mensagem', fr: 'Signer le message', de: 'Nachricht signieren', zh: '签署消息', ja: 'メッセージに署名', ko: '메시지 서명', ar: 'توقيع الرسالة', hi: 'संदेश पर हस्ताक्षर', bn: 'বার্তায় স্বাক্ষর', id: 'Tandatangani pesan', ur: 'پیغام پر دستخط' },
  wv_no_metamask: {
    es: 'No detectamos MetaMask en este navegador. Instala MetaMask y recarga la página.',
    en: "We can't detect MetaMask in this browser. Install MetaMask and reload.",
    pt: 'Não detectamos MetaMask neste navegador. Instale e recarregue.',
    fr: "Nous ne détectons pas MetaMask. Installez MetaMask et rechargez.",
    de: 'MetaMask wurde nicht erkannt. Installieren Sie MetaMask und laden Sie neu.',
    zh: '此浏览器未检测到MetaMask。请安装并刷新。',
    ja: 'このブラウザでMetaMaskが検出されません。インストールして再読み込みしてください。',
    ko: '이 브라우저에서 MetaMask가 감지되지 않습니다. 설치 후 새로고침하세요.',
    ar: 'لم نكتشف MetaMask. ثبّته ثم أعد التحميل.',
    hi: 'इस ब्राउज़र में MetaMask नहीं मिला। इंस्टॉल करें और रीलोड करें।',
    bn: 'এই ব্রাউজারে MetaMask পাওয়া যায়নি। ইনস্টল করে রিলোড করুন।',
    id: 'MetaMask tidak terdeteksi. Pasang dan muat ulang.',
    ur: 'اس براؤزر میں MetaMask نہیں ملا۔ انسٹال کریں اور دوبارہ لوڈ کریں۔',
  },
  wv_connected_as: { es: 'Conectado como', en: 'Connected as', pt: 'Conectado como', fr: 'Connecté en tant que', de: 'Verbunden als', zh: '已连接为', ja: '接続済み:', ko: '연결됨:', ar: 'متصل كـ', hi: 'के रूप में जुड़ा', bn: 'সংযুক্ত হিসাবে', id: 'Terhubung sebagai', ur: 'کے طور پر جڑا' },
  wv_chain_wrong: {
    es: 'Cambia tu wallet a BNB Smart Chain para continuar.',
    en: 'Switch your wallet to BNB Smart Chain to continue.',
    pt: 'Mude sua wallet para BNB Smart Chain.',
    fr: 'Passez votre wallet sur BNB Smart Chain.',
    de: 'Wechseln Sie zu BNB Smart Chain.',
    zh: '请将钱包切换到BNB Smart Chain。',
    ja: 'ウォレットをBNB Smart Chainに切り替えてください。',
    ko: '지갑을 BNB Smart Chain으로 전환하세요.',
    ar: 'بدّل المحفظة إلى BNB Smart Chain.',
    hi: 'अपनी वॉलेट BNB Smart Chain में बदलें।',
    bn: 'আপনার ওয়ালেট BNB Smart Chain এ স্যুইচ করুন।',
    id: 'Pindahkan dompet ke BNB Smart Chain.',
    ur: 'اپنے والیٹ کو BNB Smart Chain پر منتقل کریں۔',
  },
  wv_signed_ok: {
    es: '✅ Firma válida — eres dueño de esta wallet',
    en: '✅ Signature valid — you own this wallet',
    pt: '✅ Assinatura válida — você é dono desta wallet',
    fr: '✅ Signature valide — vous possédez ce wallet',
    de: '✅ Signatur gültig — Sie besitzen diese Wallet',
    zh: '✅ 签名有效 — 你拥有此钱包',
    ja: '✅ 署名有効 — このウォレットの所有者です',
    ko: '✅ 서명 유효 — 이 지갑의 소유자입니다',
    ar: '✅ توقيع صالح — أنت مالك هذه المحفظة',
    hi: '✅ हस्ताक्षर मान्य — यह वॉलेट आपका है',
    bn: '✅ স্বাক্ষর বৈধ — এই ওয়ালেটের মালিক আপনি',
    id: '✅ Tanda tangan valid — Anda pemilik dompet ini',
    ur: '✅ دستخط درست — یہ والیٹ آپ کا ہے',
  },
  wv_signed_fail: {
    es: '❌ La firma no coincide con la wallet conectada',
    en: '❌ Signature does not match the connected wallet',
    pt: '❌ A assinatura não corresponde à wallet conectada',
    fr: "❌ La signature ne correspond pas au wallet connecté",
    de: '❌ Signatur stimmt nicht mit verbundener Wallet überein',
    zh: '❌ 签名与已连接钱包不匹配',
    ja: '❌ 署名が接続中のウォレットと一致しません',
    ko: '❌ 서명이 연결된 지갑과 일치하지 않습니다',
    ar: '❌ التوقيع لا يطابق المحفظة المتصلة',
    hi: '❌ हस्ताक्षर जुड़े वॉलेट से मेल नहीं खाते',
    bn: '❌ স্বাক্ষর সংযুক্ত ওয়ালেটের সাথে মেলে না',
    id: '❌ Tanda tangan tidak cocok dengan dompet terhubung',
    ur: '❌ دستخط جڑے والیٹ سے میل نہیں کھاتا',
  },
  wv_user_cancel: {
    es: 'Firma cancelada por el usuario.',
    en: 'Signature cancelled by user.',
    pt: 'Assinatura cancelada pelo usuário.',
    fr: "Signature annulée par l'utilisateur.",
    de: 'Signatur vom Benutzer abgebrochen.',
    zh: '用户取消了签名。',
    ja: 'ユーザーが署名をキャンセルしました。',
    ko: '사용자가 서명을 취소했습니다.',
    ar: 'أُلغي التوقيع من قِبل المستخدم.',
    hi: 'उपयोगकर्ता ने हस्ताक्षर रद्द किया।',
    bn: 'ব্যবহারকারী স্বাক্ষর বাতিল করেছেন।',
    id: 'Tanda tangan dibatalkan pengguna.',
    ur: 'صارف نے دستخط منسوخ کیا۔',
  },
  wv_known_in_irys: {
    es: 'Esta wallet ya está vinculada en Irys con el rango',
    en: 'This wallet is already linked on Irys with rank',
    pt: 'Esta wallet já está vinculada no Irys com rank',
    fr: 'Ce wallet est déjà lié sur Irys avec le rang',
    de: 'Diese Wallet ist bereits auf Irys mit Rang verknüpft',
    zh: '此钱包已在Irys链接,等级',
    ja: 'このウォレットはIrysでランクとリンク済み',
    ko: '이 지갑은 이미 Irys에 다음 등급으로 연결됨',
    ar: 'هذه المحفظة مرتبطة بالفعل على Irys برتبة',
    hi: 'यह वॉलेट पहले से Irys पर रैंक के साथ जुड़ा है',
    bn: 'এই ওয়ালেট ইতিমধ্যে Irys-এ র্যাঙ্ক সহ যুক্ত',
    id: 'Dompet ini sudah tertaut di Irys dengan rank',
    ur: 'یہ والیٹ پہلے سے Irys پر درجہ کے ساتھ جڑا ہے',
  },

  /* ── Synergix Chat (phases B + C) ──────────────────────────── */
  ch_eye: {
    es: 'Habla con el Oráculo', en: 'Talk to the Oracle', pt: 'Fale com o Oráculo',
    fr: "Parlez à l'Oracle", de: 'Sprich mit dem Orakel', zh: '与神谕对话',
    ja: 'オラクルと話す', ko: '오라클과 대화', ar: 'تحدث مع العرّاف',
    hi: 'ऑरेकल से बात करें', bn: 'ওরাকলের সাথে কথা বলুন', id: 'Bicara dengan Oracle', ur: 'اوریکل سے بات کریں',
  },
  ch_title: {
    es: 'Synergix en vivo', en: 'Synergix live', pt: 'Synergix ao vivo',
    fr: 'Synergix en direct', de: 'Synergix live', zh: 'Synergix 在线',
    ja: 'Synergix ライブ', ko: 'Synergix 라이브', ar: 'Synergix مباشر',
    hi: 'Synergix लाइव', bn: 'Synergix লাইভ', id: 'Synergix langsung', ur: 'Synergix لائیو',
  },
  ch_sub: {
    es: 'Conversa con la inteligencia colectiva. En modo nube usa el Pensador real con RAG sobre la Memoria Inmortal; en modo local corre un modelo en TU navegador (WebGPU), sin servidor.',
    en: 'Chat with the collective intelligence. Cloud mode uses the real Thinker with RAG over the Immortal Memory; local mode runs a model in YOUR browser (WebGPU), no server.',
    pt: 'Converse com a inteligência coletiva. O modo nuvem usa o Pensador real com RAG sobre a Memória Imortal; o modo local roda um modelo no SEU navegador (WebGPU), sem servidor.',
    fr: "Discutez avec l'intelligence collective. Le mode cloud utilise le vrai Penseur avec RAG sur la Mémoire Immortelle ; le mode local exécute un modèle dans VOTRE navigateur (WebGPU), sans serveur.",
    de: 'Sprich mit der kollektiven Intelligenz. Cloud-Modus nutzt den echten Denker mit RAG über das Unsterbliche Gedächtnis; lokaler Modus führt ein Modell in DEINEM Browser (WebGPU) aus, ohne Server.',
    zh: '与集体智能对话。云端模式使用真正的思考者与不朽记忆的RAG；本地模式在你的浏览器中运行模型(WebGPU),无需服务器。',
    ja: '集合知と対話。クラウドモードは不死のメモリ上のRAGで本物のシンカーを使用、ローカルモードはあなたのブラウザ(WebGPU)でモデルを実行、サーバー不要。',
    ko: '집단 지성과 대화하세요. 클라우드 모드는 불멸의 기억에 대한 RAG로 실제 싱커를 사용하고, 로컬 모드는 당신의 브라우저(WebGPU)에서 모델을 실행합니다.',
    ar: 'تحدث مع الذكاء الجماعي. الوضع السحابي يستخدم المفكّر الحقيقي مع RAG على الذاكرة الخالدة؛ الوضع المحلي يشغّل نموذجًا في متصفحك (WebGPU) بدون خادم.',
    hi: 'सामूहिक बुद्धि से बात करें। क्लाउड मोड अमर स्मृति पर RAG के साथ असली थिंकर का उपयोग करता है; लोकल मोड आपके ब्राउज़र (WebGPU) में मॉडल चलाता है।',
    bn: 'সমষ্টিগত বুদ্ধিমত্তার সাথে কথা বলুন। ক্লাউড মোড অমর স্মৃতিতে RAG সহ আসল থিঙ্কার ব্যবহার করে; লোকাল মোড আপনার ব্রাউজারে (WebGPU) মডেল চালায়।',
    id: 'Mengobrol dengan kecerdasan kolektif. Mode cloud memakai Thinker asli dengan RAG atas Memori Abadi; mode lokal menjalankan model di browser ANDA (WebGPU), tanpa server.',
    ur: 'اجتماعی ذہانت سے بات کریں۔ کلاؤڈ موڈ امر یاد پر RAG کے ساتھ اصل تھنکر استعمال کرتا ہے؛ لوکل موڈ آپ کے براؤزر (WebGPU) میں ماڈل چلاتا ہے۔',
  },
  ch_mode_cloud: { es: '☁️ Nube (RAG real)', en: '☁️ Cloud (real RAG)', pt: '☁️ Nuvem (RAG real)', fr: '☁️ Cloud (RAG réel)', de: '☁️ Cloud (echtes RAG)', zh: '☁️ 云端 (真RAG)', ja: '☁️ クラウド (実RAG)', ko: '☁️ 클라우드 (실제 RAG)', ar: '☁️ سحابة (RAG حقيقي)', hi: '☁️ क्लाउड (असली RAG)', bn: '☁️ ক্লাউড (আসল RAG)', id: '☁️ Cloud (RAG asli)', ur: '☁️ کلاؤڈ (اصل RAG)' },
  ch_mode_local: { es: '🖥️ Local (tu navegador)', en: '🖥️ Local (your browser)', pt: '🖥️ Local (seu navegador)', fr: '🖥️ Local (votre navigateur)', de: '🖥️ Lokal (dein Browser)', zh: '🖥️ 本地 (你的浏览器)', ja: '🖥️ ローカル (あなたのブラウザ)', ko: '🖥️ 로컬 (브라우저)', ar: '🖥️ محلي (متصفحك)', hi: '🖥️ लोकल (आपका ब्राउज़र)', bn: '🖥️ লোকাল (আপনার ব্রাউজার)', id: '🖥️ Lokal (browser Anda)', ur: '🖥️ لوکل (آپ کا براؤزر)' },
  ch_hint_cloud: {
    es: 'Conectado al Pensador de Synergix con RAG sobre la Memoria Inmortal.',
    en: 'Connected to the Synergix Thinker with RAG over the Immortal Memory.',
    pt: 'Conectado ao Pensador Synergix com RAG sobre a Memória Imortal.',
    fr: 'Connecté au Penseur Synergix avec RAG sur la Mémoire Immortelle.',
    de: 'Verbunden mit dem Synergix-Denker mit RAG über das Unsterbliche Gedächtnis.',
    zh: '已连接 Synergix 思考者,带不朽记忆的RAG。',
    ja: '不死のメモリ上のRAGでSynergixシンカーに接続済み。',
    ko: '불멸의 기억 RAG와 함께 Synergix 싱커에 연결됨.',
    ar: 'متصل بمفكّر Synergix مع RAG على الذاكرة الخالدة.',
    hi: 'अमर स्मृति पर RAG के साथ Synergix थिंकर से जुड़ा।',
    bn: 'অমর স্মৃতিতে RAG সহ Synergix থিঙ্কারের সাথে সংযুক্ত।',
    id: 'Terhubung ke Thinker Synergix dengan RAG atas Memori Abadi.',
    ur: 'امر یاد پر RAG کے ساتھ Synergix تھنکر سے جڑا۔',
  },
  ch_hint_local: {
    es: 'Carga un modelo para correr Synergix 100% en tu navegador (se descarga una vez).',
    en: 'Load a model to run Synergix 100% in your browser (downloads once).',
    pt: 'Carregue um modelo para rodar o Synergix 100% no seu navegador (baixa uma vez).',
    fr: 'Chargez un modèle pour exécuter Synergix 100 % dans votre navigateur (téléchargé une fois).',
    de: 'Lade ein Modell, um Synergix zu 100% im Browser auszuführen (einmaliger Download).',
    zh: '加载模型以在浏览器中100%运行Synergix(只下载一次)。',
    ja: 'モデルを読み込んでSynergixをブラウザで100%実行(ダウンロードは一度だけ)。',
    ko: '모델을 로드하여 브라우저에서 Synergix를 100% 실행하세요 (한 번만 다운로드).',
    ar: 'حمّل نموذجًا لتشغيل Synergix بنسبة 100% في متصفحك (تنزيل لمرة واحدة).',
    hi: 'मॉडल लोड करें ताकि Synergix आपके ब्राउज़र में 100% चले (एक बार डाउनलोड)।',
    bn: 'মডেল লোড করুন যাতে Synergix আপনার ব্রাউজারে 100% চলে (একবার ডাউনলোড)।',
    id: 'Muat model untuk menjalankan Synergix 100% di browser (unduh sekali).',
    ur: 'ماڈل لوڈ کریں تاکہ Synergix آپ کے براؤزر میں 100% چلے (ایک بار ڈاؤن لوڈ)۔',
  },
  ch_hint_local_ready: {
    es: '✅ Modelo cargado — Synergix corre en tu navegador.',
    en: '✅ Model loaded — Synergix runs in your browser.',
    pt: '✅ Modelo carregado — Synergix roda no seu navegador.',
    fr: '✅ Modèle chargé — Synergix tourne dans votre navigateur.',
    de: '✅ Modell geladen — Synergix läuft im Browser.',
    zh: '✅ 模型已加载 — Synergix 在你的浏览器中运行。',
    ja: '✅ モデル読み込み完了 — Synergixがブラウザで動作中。',
    ko: '✅ 모델 로드됨 — Synergix가 브라우저에서 실행 중.',
    ar: '✅ تم تحميل النموذج — Synergix يعمل في متصفحك.',
    hi: '✅ मॉडल लोड हुआ — Synergix आपके ब्राउज़र में चल रहा है।',
    bn: '✅ মডেল লোড হয়েছে — Synergix আপনার ব্রাউজারে চলছে।',
    id: '✅ Model dimuat — Synergix berjalan di browser Anda.',
    ur: '✅ ماڈل لوڈ ہو گیا — Synergix آپ کے براؤزر میں چل رہا ہے۔',
  },
  ch_cloud_unavailable: {
    es: 'El modo nube no está disponible ahora. Usa el modo local.',
    en: 'Cloud mode is not available right now. Use local mode.',
    pt: 'O modo nuvem não está disponível agora. Use o modo local.',
    fr: "Le mode cloud n'est pas disponible. Utilisez le mode local.",
    de: 'Cloud-Modus ist derzeit nicht verfügbar. Nutze den lokalen Modus.',
    zh: '云端模式当前不可用。请使用本地模式。',
    ja: 'クラウドモードは現在利用できません。ローカルモードを使用してください。',
    ko: '클라우드 모드를 현재 사용할 수 없습니다. 로컬 모드를 사용하세요.',
    ar: 'الوضع السحابي غير متاح الآن. استخدم الوضع المحلي.',
    hi: 'क्लाउड मोड अभी उपलब्ध नहीं है। लोकल मोड का उपयोग करें।',
    bn: 'ক্লাউড মোড এখন উপলব্ধ নয়। লোকাল মোড ব্যবহার করুন।',
    id: 'Mode cloud tidak tersedia sekarang. Gunakan mode lokal.',
    ur: 'کلاؤڈ موڈ ابھی دستیاب نہیں۔ لوکل موڈ استعمال کریں۔',
  },
  ch_no_webgpu: {
    es: 'Tu navegador no soporta WebGPU. Usa Chrome/Edge de escritorio para el modo local, o cambia al modo nube.',
    en: 'Your browser lacks WebGPU. Use desktop Chrome/Edge for local mode, or switch to cloud mode.',
    pt: 'Seu navegador não suporta WebGPU. Use Chrome/Edge no desktop para o modo local, ou troque para nuvem.',
    fr: "Votre navigateur ne supporte pas WebGPU. Utilisez Chrome/Edge bureau pour le mode local, ou passez au cloud.",
    de: 'Dein Browser unterstützt kein WebGPU. Nutze Desktop-Chrome/Edge für den lokalen Modus oder wechsle zur Cloud.',
    zh: '你的浏览器不支持WebGPU。本地模式请用桌面版Chrome/Edge,或切换到云端模式。',
    ja: 'ブラウザがWebGPUに非対応です。ローカルモードはデスクトップのChrome/Edgeを使うか、クラウドモードに切り替えてください。',
    ko: '브라우저가 WebGPU를 지원하지 않습니다. 로컬 모드는 데스크톱 Chrome/Edge를 사용하거나 클라우드 모드로 전환하세요.',
    ar: 'متصفحك لا يدعم WebGPU. استخدم Chrome/Edge على سطح المكتب للوضع المحلي، أو بدّل إلى الوضع السحابي.',
    hi: 'आपका ब्राउज़र WebGPU सपोर्ट नहीं करता। लोकल मोड के लिए डेस्कटॉप Chrome/Edge उपयोग करें, या क्लाउड मोड पर जाएं।',
    bn: 'আপনার ব্রাউজার WebGPU সমর্থন করে না। লোকাল মোডের জন্য ডেস্কটপ Chrome/Edge ব্যবহার করুন, বা ক্লাউড মোডে যান।',
    id: 'Browser Anda tidak mendukung WebGPU. Gunakan Chrome/Edge desktop untuk mode lokal, atau beralih ke cloud.',
    ur: 'آپ کا براؤزر WebGPU سپورٹ نہیں کرتا۔ لوکل موڈ کے لیے ڈیسک ٹاپ Chrome/Edge استعمال کریں، یا کلاؤڈ موڈ پر جائیں۔',
  },
  ch_model: { es: 'Modelo', en: 'Model', pt: 'Modelo', fr: 'Modèle', de: 'Modell', zh: '模型', ja: 'モデル', ko: '모델', ar: 'النموذج', hi: 'मॉडल', bn: 'মডেল', id: 'Model', ur: 'ماڈل' },
  ch_load_model: { es: 'Cargar modelo', en: 'Load model', pt: 'Carregar modelo', fr: 'Charger le modèle', de: 'Modell laden', zh: '加载模型', ja: 'モデルを読み込む', ko: '모델 로드', ar: 'تحميل النموذج', hi: 'मॉडल लोड करें', bn: 'মডেল লোড', id: 'Muat model', ur: 'ماڈل لوڈ کریں' },
  ch_model_ready: { es: '✅ Listo', en: '✅ Ready', pt: '✅ Pronto', fr: '✅ Prêt', de: '✅ Bereit', zh: '✅ 就绪', ja: '✅ 準備完了', ko: '✅ 준비됨', ar: '✅ جاهز', hi: '✅ तैयार', bn: '✅ প্রস্তুত', id: '✅ Siap', ur: '✅ تیار' },
  ch_model_fail: { es: '❌ Falló la carga del modelo', en: '❌ Model load failed', pt: '❌ Falha ao carregar', fr: '❌ Échec du chargement', de: '❌ Laden fehlgeschlagen', zh: '❌ 模型加载失败', ja: '❌ 読み込み失敗', ko: '❌ 로드 실패', ar: '❌ فشل التحميل', hi: '❌ लोड विफल', bn: '❌ লোড ব্যর্থ', id: '❌ Gagal memuat', ur: '❌ لوڈ ناکام' },
  ch_placeholder: { es: 'Pregunta lo que sea…', en: 'Ask anything…', pt: 'Pergunte qualquer coisa…', fr: 'Demandez n\'importe quoi…', de: 'Frag irgendetwas…', zh: '问任何问题…', ja: '何でも聞いてください…', ko: '무엇이든 물어보세요…', ar: 'اسأل أي شيء…', hi: 'कुछ भी पूछें…', bn: 'যা খুশি জিজ্ঞাসা করুন…', id: 'Tanya apa saja…', ur: 'کچھ بھی پوچھیں…' },
  ch_send: { es: 'Enviar', en: 'Send', pt: 'Enviar', fr: 'Envoyer', de: 'Senden', zh: '发送', ja: '送信', ko: '전송', ar: 'إرسال', hi: 'भेजें', bn: 'পাঠান', id: 'Kirim', ur: 'بھیجیں' },
  ch_greeting: {
    es: 'Soy Synergix. Pregunta y razonaré sobre la Memoria Inmortal de la red.',
    en: 'I am Synergix. Ask, and I will reason over the network\'s Immortal Memory.',
    pt: 'Sou Synergix. Pergunte e raciocinarei sobre a Memória Imortal da rede.',
    fr: "Je suis Synergix. Demandez, et je raisonnerai sur la Mémoire Immortelle du réseau.",
    de: 'Ich bin Synergix. Frag, und ich denke über das Unsterbliche Gedächtnis des Netzwerks nach.',
    zh: '我是 Synergix。提问,我将基于网络的不朽记忆进行推理。',
    ja: '私はSynergixです。質問してください、ネットワークの不死のメモリを基に推論します。',
    ko: '저는 Synergix입니다. 물어보시면 네트워크의 불멸의 기억을 바탕으로 추론합니다.',
    ar: 'أنا Synergix. اسأل وسأحلّل بناءً على الذاكرة الخالدة للشبكة.',
    hi: 'मैं Synergix हूँ। पूछें, और मैं नेटवर्क की अमर स्मृति पर तर्क करूँगा।',
    bn: 'আমি Synergix। জিজ্ঞাসা করুন, আমি নেটওয়ার্কের অমর স্মৃতির উপর যুক্তি করব।',
    id: 'Saya Synergix. Bertanyalah, dan saya akan bernalar atas Memori Abadi jaringan.',
    ur: 'میں Synergix ہوں۔ پوچھیں، میں نیٹ ورک کی امر یاد پر استدلال کروں گا۔',
  },
  ch_memory_used: {
    es: '📜 {n} fragmentos de la Memoria Inmortal usados',
    en: '📜 {n} Immortal Memory fragments used',
    pt: '📜 {n} fragmentos da Memória Imortal usados',
    fr: '📜 {n} fragments de la Mémoire Immortelle utilisés',
    de: '📜 {n} Fragmente des Unsterblichen Gedächtnisses verwendet',
    zh: '📜 使用了 {n} 个不朽记忆片段',
    ja: '📜 {n} 件の不死のメモリ断片を使用',
    ko: '📜 불멸의 기억 조각 {n}개 사용',
    ar: '📜 تم استخدام {n} أجزاء من الذاكرة الخالدة',
    hi: '📜 अमर स्मृति के {n} अंश उपयोग किए',
    bn: '📜 {n} টি অমর স্মৃতির খণ্ড ব্যবহৃত',
    id: '📜 {n} fragmen Memori Abadi digunakan',
    ur: '📜 {n} امر یاد کے ٹکڑے استعمال ہوئے',
  },
  ch_error: { es: 'Algo falló. Reintenta.', en: 'Something went wrong. Retry.', pt: 'Algo deu errado. Tente de novo.', fr: 'Une erreur est survenue. Réessayez.', de: 'Etwas ist schiefgelaufen. Erneut versuchen.', zh: '出错了。请重试。', ja: 'エラーが発生しました。再試行してください。', ko: '문제가 발생했습니다. 다시 시도하세요.', ar: 'حدث خطأ. أعد المحاولة.', hi: 'कुछ गलत हुआ। पुनः प्रयास करें।', bn: 'কিছু ভুল হয়েছে। আবার চেষ্টা করুন।', id: 'Ada yang salah. Coba lagi.', ur: 'کچھ غلط ہوا۔ دوبارہ کوشش کریں۔' },
  ch_disclaimer: {
    es: 'Synergix es una IA experimental. En modo local, la calidad depende del modelo que corre tu navegador.',
    en: 'Synergix is experimental AI. In local mode, quality depends on the model running in your browser.',
    pt: 'Synergix é IA experimental. No modo local, a qualidade depende do modelo no seu navegador.',
    fr: "Synergix est une IA expérimentale. En mode local, la qualité dépend du modèle de votre navigateur.",
    de: 'Synergix ist experimentelle KI. Im lokalen Modus hängt die Qualität vom Browser-Modell ab.',
    zh: 'Synergix 是实验性 AI。本地模式下,质量取决于浏览器运行的模型。',
    ja: 'Synergixは実験的AIです。ローカルモードでは品質はブラウザで動くモデルに依存します。',
    ko: 'Synergix는 실험적 AI입니다. 로컬 모드에서는 품질이 브라우저 모델에 따라 달라집니다.',
    ar: 'Synergix ذكاء اصطناعي تجريبي. في الوضع المحلي، تعتمد الجودة على النموذج في متصفحك.',
    hi: 'Synergix प्रायोगिक AI है। लोकल मोड में, गुणवत्ता आपके ब्राउज़र के मॉडल पर निर्भर करती है।',
    bn: 'Synergix পরীক্ষামূলক AI। লোকাল মোডে, গুণমান আপনার ব্রাউজারের মডেলের উপর নির্ভর করে।',
    id: 'Synergix adalah AI eksperimental. Dalam mode lokal, kualitas bergantung pada model di browser Anda.',
    ur: 'Synergix تجرباتی AI ہے۔ لوکل موڈ میں، معیار آپ کے براؤزر کے ماڈل پر منحصر ہے۔',
  },

  /* ── Contribute (phase B) ──────────────────────────────────── */
  co_eye: {
    es: 'Inmortaliza tu sabiduría', en: 'Immortalize your wisdom', pt: 'Imortalize sua sabedoria',
    fr: 'Immortalisez votre sagesse', de: 'Verewige deine Weisheit', zh: '永存你的智慧',
    ja: 'あなたの知恵を永遠に', ko: '당신의 지혜를 불멸로', ar: 'خلّد حكمتك',
    hi: 'अपनी बुद्धि अमर करें', bn: 'আপনার জ্ঞান অমর করুন', id: 'Abadikan kebijaksanaan Anda', ur: 'اپنی حکمت کو امر بنائیں',
  },
  co_title: {
    es: 'Contribuye desde la web', en: 'Contribute from the web', pt: 'Contribua pela web',
    fr: 'Contribuez depuis le web', de: 'Beitrag aus dem Web', zh: '从网页贡献',
    ja: 'ウェブから貢献', ko: '웹에서 기여하기', ar: 'ساهم من الويب',
    hi: 'वेब से योगदान करें', bn: 'ওয়েব থেকে অবদান', id: 'Berkontribusi dari web', ur: 'ویب سے شراکت کریں',
  },
  co_sub: {
    es: 'Tu aporte es evaluado por el Juez de Synergix. Si supera 6.0, firmas con tu wallet y se sella para siempre en Irys — con tu firma on-chain como autor.',
    en: 'Your contribution is scored by the Synergix Judge. If it clears 6.0, you sign with your wallet and it is sealed forever on Irys — with your on-chain signature as author.',
    pt: 'Sua contribuição é avaliada pelo Juiz Synergix. Se passar de 6.0, você assina com sua wallet e é selada para sempre no Irys — com sua assinatura on-chain como autor.',
    fr: "Votre contribution est notée par le Juge Synergix. Au-dessus de 6.0, vous signez avec votre wallet et elle est scellée à jamais sur Irys — avec votre signature on-chain comme auteur.",
    de: 'Dein Beitrag wird vom Synergix-Richter bewertet. Über 6.0 signierst du mit deiner Wallet und er wird für immer auf Irys versiegelt — mit deiner On-Chain-Signatur als Autor.',
    zh: '你的贡献由 Synergix 评判官打分。若超过6.0,你用钱包签名,它将永久封存于 Irys — 以你的链上签名作为作者。',
    ja: 'あなたの貢献はSynergixジャッジが採点します。6.0を超えればウォレットで署名し、Irysに永久封印されます — 作者としてのオンチェーン署名付き。',
    ko: '당신의 기여는 Synergix 심판이 채점합니다. 6.0을 넘으면 지갑으로 서명하고 Irys에 영원히 봉인됩니다 — 작성자로서 온체인 서명과 함께.',
    ar: 'مساهمتك يقيّمها قاضي Synergix. إذا تجاوزت 6.0، توقّع بمحفظتك وتُختَم للأبد على Irys — بتوقيعك على السلسلة كمؤلف.',
    hi: 'आपके योगदान को Synergix जज स्कोर करता है। 6.0 पार करने पर, आप अपने वॉलेट से हस्ताक्षर करते हैं और यह Irys पर हमेशा के लिए सील हो जाता है।',
    bn: 'আপনার অবদান Synergix বিচারক স্কোর করে। 6.0 পার হলে, আপনি ওয়ালেট দিয়ে স্বাক্ষর করেন এবং এটি Irys-এ চিরতরে সিল হয়।',
    id: 'Kontribusi Anda dinilai oleh Hakim Synergix. Jika lewat 6.0, Anda tanda tangani dengan dompet dan disegel selamanya di Irys.',
    ur: 'آپ کی شراکت کو Synergix جج اسکور کرتا ہے۔ 6.0 سے زیادہ ہونے پر، آپ اپنے والیٹ سے دستخط کرتے ہیں اور یہ Irys پر ہمیشہ کے لیے مہر بند ہو جاتی ہے۔',
  },
  co_cloud_off: {
    es: 'Contribuir desde la web requiere el motor de Synergix conectado. Por ahora, usa el bot de Telegram para aportar y ganar puntos.',
    en: 'Contributing from the web requires the Synergix engine connected. For now, use the Telegram bot to contribute and earn points.',
    pt: 'Contribuir pela web requer o motor Synergix conectado. Por enquanto, use o bot do Telegram para contribuir e ganhar pontos.',
    fr: "Contribuer depuis le web nécessite le moteur Synergix connecté. Pour l'instant, utilisez le bot Telegram pour contribuer et gagner des points.",
    de: 'Beiträge aus dem Web erfordern die verbundene Synergix-Engine. Nutze vorerst den Telegram-Bot, um beizutragen und Punkte zu sammeln.',
    zh: '从网页贡献需要连接 Synergix 引擎。目前请使用 Telegram 机器人贡献并赚取积分。',
    ja: 'ウェブからの貢献にはSynergixエンジンの接続が必要です。今のところ、Telegramボットで貢献してポイントを獲得してください。',
    ko: '웹에서 기여하려면 Synergix 엔진 연결이 필요합니다. 지금은 텔레그램 봇으로 기여하고 포인트를 얻으세요.',
    ar: 'المساهمة من الويب تتطلب اتصال محرك Synergix. حاليًا، استخدم بوت Telegram للمساهمة وكسب النقاط.',
    hi: 'वेब से योगदान के लिए Synergix इंजन कनेक्ट होना चाहिए। फिलहाल, योगदान और अंक के लिए Telegram बॉट का उपयोग करें।',
    bn: 'ওয়েব থেকে অবদানের জন্য Synergix ইঞ্জিন সংযুক্ত থাকা দরকার। আপাতত, অবদান ও পয়েন্টের জন্য Telegram বট ব্যবহার করুন।',
    id: 'Berkontribusi dari web memerlukan mesin Synergix terhubung. Untuk saat ini, gunakan bot Telegram untuk berkontribusi dan dapatkan poin.',
    ur: 'ویب سے شراکت کے لیے Synergix انجن منسلک ہونا ضروری ہے۔ فی الحال، شراکت اور پوائنٹس کے لیے Telegram بوٹ استعمال کریں۔',
  },
  co_use_bot: { es: 'Usar el bot', en: 'Use the bot', pt: 'Usar o bot', fr: 'Utiliser le bot', de: 'Bot nutzen', zh: '使用机器人', ja: 'ボットを使う', ko: '봇 사용', ar: 'استخدم البوت', hi: 'बॉट उपयोग करें', bn: 'বট ব্যবহার', id: 'Gunakan bot', ur: 'بوٹ استعمال کریں' },
  co_placeholder: {
    es: 'Comparte una idea, reflexión o conocimiento original (mín. 20 caracteres)…',
    en: 'Share an original idea, reflection or piece of knowledge (min. 20 chars)…',
    pt: 'Compartilhe uma ideia, reflexão ou conhecimento original (mín. 20 caracteres)…',
    fr: "Partagez une idée, réflexion ou connaissance originale (min. 20 caractères)…",
    de: 'Teile eine originelle Idee, Reflexion oder Wissen (mind. 20 Zeichen)…',
    zh: '分享一个原创的想法、反思或知识(至少20个字符)…',
    ja: 'オリジナルのアイデア・考察・知識を共有(最低20文字)…',
    ko: '독창적인 아이디어, 성찰 또는 지식을 공유하세요 (최소 20자)…',
    ar: 'شارك فكرة أو تأملًا أو معرفة أصلية (20 حرفًا على الأقل)…',
    hi: 'एक मौलिक विचार, चिंतन या ज्ञान साझा करें (न्यूनतम 20 अक्षर)…',
    bn: 'একটি মৌলিক ধারণা, প্রতিফলন বা জ্ঞান শেয়ার করুন (ন্যূনতম 20 অক্ষর)…',
    id: 'Bagikan ide, refleksi, atau pengetahuan orisinal (min. 20 karakter)…',
    ur: 'ایک اصل خیال، غور یا علم شیئر کریں (کم از کم 20 حروف)…',
  },
  co_evaluate: { es: 'Evaluar', en: 'Evaluate', pt: 'Avaliar', fr: 'Évaluer', de: 'Bewerten', zh: '评估', ja: '評価', ko: '평가', ar: 'تقييم', hi: 'मूल्यांकन', bn: 'মূল্যায়ন', id: 'Evaluasi', ur: 'تشخیص' },
  co_evaluating: { es: 'Evaluando…', en: 'Evaluating…', pt: 'Avaliando…', fr: 'Évaluation…', de: 'Bewerte…', zh: '评估中…', ja: '評価中…', ko: '평가 중…', ar: 'جارٍ التقييم…', hi: 'मूल्यांकन हो रहा…', bn: 'মূল্যায়ন হচ্ছে…', id: 'Mengevaluasi…', ur: 'تشخیص ہو رہی…' },
  co_approved: { es: 'APROBADO', en: 'APPROVED', pt: 'APROVADO', fr: 'APPROUVÉ', de: 'GENEHMIGT', zh: '通过', ja: '承認', ko: '승인', ar: 'مقبول', hi: 'स्वीकृत', bn: 'অনুমোদিত', id: 'DISETUJUI', ur: 'منظور' },
  co_rejected: { es: 'RECHAZADO', en: 'REJECTED', pt: 'REJEITADO', fr: 'REJETÉ', de: 'ABGELEHNT', zh: '拒绝', ja: '却下', ko: '거부', ar: 'مرفوض', hi: 'अस्वीकृत', bn: 'প্রত্যাখ্যাত', id: 'DITOLAK', ur: 'مسترد' },
  co_connect_wallet: { es: 'Conectar wallet', en: 'Connect wallet', pt: 'Conectar wallet', fr: 'Connecter wallet', de: 'Wallet verbinden', zh: '连接钱包', ja: 'ウォレット接続', ko: '지갑 연결', ar: 'ربط المحفظة', hi: 'वॉलेट कनेक्ट', bn: 'ওয়ালেট সংযোগ', id: 'Hubungkan dompet', ur: 'والیٹ کنیکٹ' },
  co_sign: { es: 'Firmar como autor', en: 'Sign as author', pt: 'Assinar como autor', fr: "Signer comme auteur", de: 'Als Autor signieren', zh: '作为作者签名', ja: '作者として署名', ko: '작성자로 서명', ar: 'وقّع كمؤلف', hi: 'लेखक के रूप में हस्ताक्षर', bn: 'লেখক হিসেবে স্বাক্ষর', id: 'Tandatangani sebagai penulis', ur: 'بطور مصنف دستخط' },
  co_seal: { es: 'Sellar en Irys', en: 'Seal on Irys', pt: 'Selar no Irys', fr: 'Sceller sur Irys', de: 'Auf Irys versiegeln', zh: '封存到 Irys', ja: 'Irysに封印', ko: 'Irys에 봉인', ar: 'ختم على Irys', hi: 'Irys पर सील करें', bn: 'Irys-এ সিল করুন', id: 'Segel di Irys', ur: 'Irys پر مہر کریں' },
  co_sealing: { es: 'Sellando…', en: 'Sealing…', pt: 'Selando…', fr: 'Scellement…', de: 'Versiegle…', zh: '封存中…', ja: '封印中…', ko: '봉인 중…', ar: 'جارٍ الختم…', hi: 'सील हो रहा…', bn: 'সিল হচ্ছে…', id: 'Menyegel…', ur: 'مہر ہو رہی…' },
  co_sealed: { es: '✅ Sellado para siempre en Irys:', en: '✅ Sealed forever on Irys:', pt: '✅ Selado para sempre no Irys:', fr: '✅ Scellé à jamais sur Irys:', de: '✅ Für immer auf Irys versiegelt:', zh: '✅ 已永久封存于 Irys:', ja: '✅ Irysに永久封印:', ko: '✅ Irys에 영원히 봉인됨:', ar: '✅ مختوم للأبد على Irys:', hi: '✅ Irys पर हमेशा के लिए सील:', bn: '✅ Irys-এ চিরতরে সিল:', id: '✅ Disegel selamanya di Irys:', ur: '✅ Irys پر ہمیشہ کے لیے مہر بند:' },
  co_too_short: { es: 'Mínimo 20 caracteres.', en: 'Minimum 20 characters.', pt: 'Mínimo 20 caracteres.', fr: 'Minimum 20 caractères.', de: 'Mindestens 20 Zeichen.', zh: '至少20个字符。', ja: '最低20文字です。', ko: '최소 20자입니다.', ar: '20 حرفًا على الأقل.', hi: 'न्यूनतम 20 अक्षर।', bn: 'ন্যূনতম 20 অক্ষর।', id: 'Minimal 20 karakter.', ur: 'کم از کم 20 حروف۔' },
  co_eval_error: { es: 'No se pudo evaluar. Reintenta.', en: 'Could not evaluate. Retry.', pt: 'Não foi possível avaliar. Tente de novo.', fr: "Impossible d'évaluer. Réessayez.", de: 'Bewertung fehlgeschlagen. Erneut versuchen.', zh: '无法评估。请重试。', ja: '評価できませんでした。再試行してください。', ko: '평가할 수 없습니다. 다시 시도하세요.', ar: 'تعذّر التقييم. أعد المحاولة.', hi: 'मूल्यांकन नहीं हो सका। पुनः प्रयास करें।', bn: 'মূল্যায়ন করা যায়নি। আবার চেষ্টা করুন।', id: 'Tidak dapat mengevaluasi. Coba lagi.', ur: 'تشخیص نہ ہو سکی۔ دوبارہ کوشش کریں۔' },
  co_seal_error: { es: 'No se pudo sellar. Reintenta.', en: 'Could not seal. Retry.', pt: 'Não foi possível selar. Tente de novo.', fr: 'Impossible de sceller. Réessayez.', de: 'Versiegeln fehlgeschlagen. Erneut versuchen.', zh: '无法封存。请重试。', ja: '封印できませんでした。再試行してください。', ko: '봉인할 수 없습니다. 다시 시도하세요.', ar: 'تعذّر الختم. أعد المحاولة.', hi: 'सील नहीं हो सका। पुनः प्रयास करें।', bn: 'সিল করা যায়নি। আবার চেষ্টা করুন।', id: 'Tidak dapat menyegel. Coba lagi.', ur: 'مہر نہ ہو سکی۔ دوبارہ کوشش کریں۔' },
  co_rejected_seal: {
    es: 'El Juez rechazó este aporte. Mejóralo con el feedback y reintenta.',
    en: 'The Judge rejected this contribution. Improve it with the feedback and retry.',
    pt: 'O Juiz rejeitou esta contribuição. Melhore com o feedback e tente de novo.',
    fr: "Le Juge a rejeté cette contribution. Améliorez-la avec le retour et réessayez.",
    de: 'Der Richter hat diesen Beitrag abgelehnt. Verbessere ihn mit dem Feedback und versuche es erneut.',
    zh: '评判官拒绝了此贡献。根据反馈改进后重试。',
    ja: 'ジャッジがこの貢献を却下しました。フィードバックを参考に改善して再試行してください。',
    ko: '심판이 이 기여를 거부했습니다. 피드백으로 개선 후 다시 시도하세요.',
    ar: 'رفض القاضي هذه المساهمة. حسّنها وفق الملاحظات وأعد المحاولة.',
    hi: 'जज ने इस योगदान को अस्वीकार किया। फीडबैक से सुधारें और पुनः प्रयास करें।',
    bn: 'বিচারক এই অবদান প্রত্যাখ্যান করেছেন। ফিডব্যাক দিয়ে উন্নত করে আবার চেষ্টা করুন।',
    id: 'Hakim menolak kontribusi ini. Perbaiki dengan masukan dan coba lagi.',
    ur: 'جج نے یہ شراکت مسترد کر دی۔ فیڈ بیک سے بہتر بنائیں اور دوبارہ کوشش کریں۔',
  },
};

/* Merge the chat/contribute strings into the base table. */
let currentLang = 'en';

export function setI18nLang(code) {
  currentLang = code || 'en';
}

export function getI18nLang() {
  return currentLang;
}

/** Look up a translation by key in the current language. */
export function t(key) {
  const entry = STRINGS[key];
  if (!entry) return key;
  return entry[currentLang] ?? entry.en ?? Object.values(entry)[0] ?? key;
}

/**
 * Re-render every [data-st-i18n] node within `root`.
 * Honors `data-st-html="true"` to use innerHTML when the string
 * contains markup (we keep that opt-in to avoid accidental XSS).
 */
export function applyI18n(root = document) {
  const nodes = root.querySelectorAll?.('[data-st-i18n]') ?? [];
  for (const node of nodes) {
    const key = node.getAttribute('data-st-i18n');
    const txt = t(key);
    if (node.dataset.stHtml === 'true') {
      node.innerHTML = txt;
    } else {
      node.textContent = txt;
    }
  }
}

/* ── Wire-up against the existing language bar ──────────────────
 * The page's existing `.lb` buttons toggle the global LANG; we
 * piggy-back on those clicks so the new widgets follow the same
 * language without needing to expose any extra API.
 */
export function bindLanguageBar() {
  // Initial detection: <html lang="..."> or any .lb.on
  const initial =
    document.querySelector('.lb.on')?.getAttribute('data-lang') ||
    document.documentElement.lang ||
    'en';
  setI18nLang(initial);

  // Re-render on each click
  document.addEventListener('click', (e) => {
    const btn = e.target.closest?.('.lb');
    if (!btn) return;
    const code = btn.getAttribute('data-lang');
    if (code) {
      setI18nLang(code);
      applyI18n(document);
      window.dispatchEvent(
        new CustomEvent('synergix:langchange', { detail: { lang: code } }),
      );
    }
  });

  // Custom event for external triggers
  window.addEventListener('synergix:setlang', (e) => {
    if (e.detail?.lang) {
      setI18nLang(e.detail.lang);
      applyI18n(document);
    }
  });
}
