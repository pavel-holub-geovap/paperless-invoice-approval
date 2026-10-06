import { describe, expect, it } from "vitest";
import { decimalString } from "./decimals";
describe("Canonical financial inputs",()=>{
  it.each(["1497,38","1497.38","1 497,38","1\u00a0497,38","1.497,38","1,497.38"])("accepts %s without floating arithmetic",value=>expect(decimalString(value)).toBe("1497.38"));
  it.each(["1,2.3","1,497","1.2.3","text","1e3"])("rejects ambiguous/malformed %s",value=>{
    if(value==="1,497") expect(decimalString(value)).toBe("1.497");
    else expect(()=>decimalString(value)).toThrow();
  });
  it("keeps null separate from explicit zero",()=>{expect(decimalString(" ")).toBeNull();expect(decimalString("0,00")).toBe("0.00");expect(decimalString("-0,01")).toBe("-0.01");});
});
