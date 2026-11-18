import bundle from '../generated/snapshot.json';

export type SnapshotBundle = typeof bundle;
export const snapshotData = bundle;

const intFmt = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 });
const decFmt = new Intl.NumberFormat('id-ID', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMetricValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return '—';
  const isRatio = unit.includes('/') || unit.includes('%') || !Number.isInteger(value);
  return isRatio ? decFmt.format(value) : intFmt.format(value);
}

export function formatShortUnit(unit: string): string {
  if (unit === 'orang/100.000 penduduk') return '/ 100rb pddk';
  if (unit === 'fasilitas/100.000 penduduk') return '/ 100rb pddk';
  return unit;
}

export function statusLabel(status: string, missingReason?: string | null): string {
  switch (status) {
    case 'validated':
      return 'Tervalidasi';
    case 'warning':
      if (missingReason === 'missing_in_source') return 'Tidak tercatat';
      return 'Catatan audit';
    case 'blocked':
      return 'Diblokir DQ';
    case 'not_comparable':
      return 'Tahun tak sepadan';
    case 'not_computable':
      return 'Tak dapat dihitung';
    default:
      return 'Belum diperiksa';
  }
}

export function statusPillClass(status: string): string {
  switch (status) {
    case 'validated':
      return 'pill pos';
    case 'warning':
      return 'pill warn';
    case 'blocked':
      return 'pill neg';
    default:
      return 'pill';
  }
}

export function missingReasonExplanation(reason?: string | null): string {
  switch (reason) {
    case 'year_mismatch':
      return 'Indikator belum dapat dihitung karena denominator penduduk terverifikasi hanya tersedia untuk tahun referensi 2024.';
    case 'denominator_not_computable':
      return 'Denominator penduduk bernilai nol atau kosong sehingga pembagian rasio dibatalkan.';
    case 'missing_in_source':
      return 'Sumber resmi tidak mencantumkan nilai pada sel wilayah/periode ini (bukan angka nol).';
    case 'blocked_quality':
      return 'Angka ditahan oleh Quality Gate karena gagal pemeriksaan konsistensi atau format numerik.';
    default:
      return 'Data belum tersedia untuk kombinasi filter ini.';
  }
}

export function shortHash(sha?: string | null): string {
  if (!sha) return '—';
  return `${sha.slice(0, 10)}…`;
}
