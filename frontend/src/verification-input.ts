/** Client presentation only; the server still verifies the immutable operation. */
export function normalizeVerificationCode(value:string):string{
 return value.normalize('NFKC').replace(/\D/g,'').slice(0,6)
}
