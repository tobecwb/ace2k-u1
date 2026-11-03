// The unit's self-test, in words.
import { t } from './i18n.js';

// The checks of the unit's self-test, as the unit names them. Each has its words in the catalogue.
export const HEALTH_NAMES = [
  'ntc_left',
  'ntc_right',
  'chamber',
  'chamber_plausible',
  'reader_a',
  'reader_b',
  'zerocross',
  'mains_hz',
  'cutout',
  'insert1',
  'insert2',
  'insert3',
  'insert4',
  'buffer1',
  'buffer2',
  'buffer3',
  'buffer4',
  'encoder1',
  'encoder2',
  'encoder3',
  'encoder4',
  'fg1',
  'fg2',
  'fg3',
  'fg4',
  'sensors_fresh',
  'vdda',
  'clock',
  'watchdog',
  'config_page',
  'image_crc',
];

// The failing checks in words, joined with a comma and a space. It is "?" when no check is named.
/** @param {string[] | null | undefined} failing */
export function healthWords(failing) {
  if (!failing?.length) return '?';
  return failing.map((n) => t(`health.${HEALTH_NAMES.includes(n) ? n : 'unknown'}`)).join(', ');
}
