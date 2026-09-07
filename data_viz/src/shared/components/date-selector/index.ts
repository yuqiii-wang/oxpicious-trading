/**
 * date-selector — shared single-trading-day picker.
 *
 * DateSelector covers every "pick one day" control: a Material Design
 * calendar with year → month → day views (pass showTime for the clock
 * too). null value = the default day (biz today / latest, via
 * `defaultDate`), a concrete value freezes that historical day, and an
 * optional `dates` roster bounds the calendar to days with data.
 */
export { default as DateSelector } from "./DateSelector";
