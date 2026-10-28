import type {Operation} from './api'
export type Product={id:string;name:string;nav_cents:number;quote_version:number;simulated:true}
export type Position={account_id:string;account_name:string;product_id:string;product_name:string;units_1e4:number;cost_cents:number;version:number;value_cents:number;gain_cents:number;reserved_units_1e4:number;available_units_1e4:number;simulated:true}
export type WealthInput={account_id:string;product_id:string;action:'purchase'|'redeem';amount_cents:number}
export type WealthSnapshot={products:Product[];positions:Position[];applications:Operation[];simulated:true;valuation_note:string}
