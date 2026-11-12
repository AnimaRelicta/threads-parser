-- 003_clean_location: по about_raw пересчитываем местоположение.
-- Казахстан -> account_location = 'Казахстан'; другая страна -> is_target=false,
-- joined_*/account_location/verified_* пустые (остаётся только about_raw).
SET search_path = instagram;

UPDATE profiles
SET account_location = 'Казахстан'
WHERE about_raw->>'text' ~* '(местоположение аккаунта|account based in)[\s ]*(республика[\s ]+)?(казахстан|kazakhstan)';

UPDATE profiles
SET is_target = false,
    account_location = NULL,
    joined_text = NULL, joined_date = NULL,
    verified_text = NULL, verified_date = NULL, is_verified = NULL
WHERE about_raw->>'text' ~* '(местоположение аккаунта|account based in)'
  AND about_raw->>'text' !~* '(местоположение аккаунта|account based in)[\s ]*(республика[\s ]+)?(казахстан|kazakhstan)';
