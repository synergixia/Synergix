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
};

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
