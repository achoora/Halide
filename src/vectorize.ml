open Ir
open Util
open List

type vec_expr = 
  | Scalar of expr
  | Const of expr * int
  | Linear of expr * int
  | Vector of expr

let vectorize_expr expr var width = 

  let rec is_vector = function
    | Scalar _ | Const _ -> false
    | _ -> true
  
  and expand = function
    | Scalar e | Const (e, _) -> Broadcast (e, width)
    | Linear (e, s) ->
      let range x = IntImm (s * x) in
      Bop (Add, Broadcast (e, width), MakeVector (map range (0 -- width)))      
    | Vector e -> e

  and unpack_scalar = function
    | Scalar e | Const (e, _) -> e
    | _ -> raise (Wtf("Can't unpack a vector into a scalar"))

  and vec expr = match expr with
    | IntImm x | UIntImm x -> Const (expr, x)
    | FloatImm _ -> Scalar expr (* Not strictly true. It's constant, but not a const int *)
    | Cast (t, expr) -> begin match (vec expr) with
        | Const (e, c)  -> Const  (Cast (t, e), c)
        | Scalar e      -> Scalar (Cast (t, e))
        | Linear (e, s) -> Linear (Cast (t, e), s)
        | Vector e      -> Vector (Cast (vector_of_val_type t width, e))
    end
    | Bop (op, a, b) -> begin
      let veca = vec a and vecb = vec b in
      match (op, veca, vecb) with
        | (_, Vector va, vb) -> Vector (Bop(op, va, expand vb))
        | (_, va, Vector vb) -> Vector (Bop(op, expand va, vb))
        | (_, Scalar va, Scalar vb) 
        | (_, Scalar va, Const (vb, _)) 
        | (_, Const (va, _), Scalar vb) -> Scalar (Bop(op, va, vb))
        | (_, Const (va, ca), Const (vb, cb))   -> begin match op with
            | Add -> Const (Bop(op, va, vb), ca + cb)
            | Sub -> Const (Bop(op, va, vb), ca - cb)
            | Mul -> Const (Bop(op, va, vb), ca * cb)
            | Div -> Const (Bop(op, va, vb), ca / cb)
        end
        | (_, Scalar va, Linear (vb, sb)) -> begin match op with
            | Add -> Linear (Bop(op, va, vb), sb)
            | Sub -> Linear (Bop(op, va, vb), -sb)
            | Mul | Div -> Vector (Bop(op, expand veca, expand vecb))
        end          
        | (_, Linear (va, sa), Scalar vb) -> begin match op with
            | Add | Sub -> Linear (Bop(op, va, vb), sa)
            | Mul | Div -> Vector (Bop(op, expand veca, expand vecb))
        end
        | (_, Const (va, ca), Linear (vb, sb))  -> begin match op with
            | Add -> Linear (Bop(op, va, vb), sb)
            | Sub -> Linear (Bop(op, va, vb), -sb)
            | Mul -> Linear (Bop(op, va, vb), sb*ca)
            | Div -> Vector (Bop(op, expand veca, expand vecb))
        end
        | (_, Linear (va, sa), Const (vb, cb))  -> begin match op with
            | Add | Sub -> Linear (Bop(op, va, vb), sa)
            | Mul -> Linear (Bop(op, va, vb), sa*cb)
            | Div -> Vector (Bop(op, expand veca, expand vecb))
        end
        | (_, Linear (va, sa), Linear (vb, sb)) -> begin match op with
            | Add ->  Linear (Bop(op, va, vb), sa + sb)
            | Sub ->  Linear (Bop(op, va, vb), sa - sb)
            | _ -> Vector (Bop(op, expand veca, expand vecb))
        end
    end
    | Cmp (op, a, b) -> 
      let veca = vec a and vecb = vec b in
      if (is_vector veca || is_vector vecb) then
        Vector(Cmp(op, expand veca, expand vecb))
      else
        Scalar(Cmp(op, a, b))
    | And (a, b) ->
      let veca = vec a and vecb = vec b in
      if (is_vector veca || is_vector vecb) then
        Vector(And(expand veca, expand vecb))
      else
        Scalar(And(a, b))
    | Or (a, b) ->
      let veca = vec a and vecb = vec b in
      if (is_vector veca || is_vector vecb) then
        Vector(Or(expand veca, expand vecb))
      else
        Scalar(Or(a, b))
    | Not (a) -> 
      let veca = vec a in
      if (is_vector veca) then
        Vector(Not(expand veca))
      else
        Scalar(Not(a))
    | Select (c, a, b) ->
      let veca = vec a and vecb = vec b and vecc = vec c in
      if (not (is_vector veca || is_vector vecb || is_vector vecc)) then
        Scalar(Select(unpack_scalar vecc, unpack_scalar veca, unpack_scalar vecb))
      else begin match (vecc, veca, vecb) with
        | (Scalar (vc), Linear (va, sa), Linear (vb, sb)) 
        | (Const (vc, _), Linear (va, sa), Linear (vb, sb)) when (sa = sb) ->
          Linear (Select(vc, va, vb), sa)
        | (Scalar (vc), _, _) | (Const (vc, _), _, _) -> 
          Vector (Select(vc, expand veca, expand vecb))
        | _ -> 
          Vector (Select(expand vecc, expand veca, expand vecb))
      end
    | Load (t, mr) -> begin let veci = (vec mr.idx) in match veci with 
        | Const (e, _) | Scalar e | Linear (e, 0) -> Scalar (Load (t, mr))
        | Linear (e, 1) -> Vector (Load (vector_of_val_type t width, mr))
        | Linear (e, _) (* TODO: strided load *)
        | Vector (e) -> Vector (Load (vector_of_val_type t width, {buf = mr.buf; idx = expand veci}))
    end
    | Var(name) when (name = var) -> Linear (expr, 1)
    | Var(_) -> Scalar(expr) 
    | _ -> raise (Wtf("Can't vectorize vector code"))
  in
  match (vec expr) with
    | Scalar(e) | Const(e, _) -> e
    | v -> expand v

(* TODO: vectorize statement *)
  

